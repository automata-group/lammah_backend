from langchain_core.prompts import ChatPromptTemplate
from app.services.llm_factory import get_llm_for_user
import json

SUMMARY_SYSTEM_PROMPT = (
    "أنت مساعد أكاديمي ذكي مهمتك مساعدة الطلاب في المذاكرة. "
    "سيتم تزويدك بـ 'النص الصوتي المفرغ' لمحاضرة، و'سياق الـ PDF' الذي يحتوي على أجزاء من سلايدات المادة مع أرقام صفحاتها. "
    "المطلوب منك استخراج المعلومات التالية ومطابقتها لإنشاء ملف PDF تفاعلي: "
    "1. التظليل (Highlights): اقتبس النصوص الدقيقة بالنص الحرفي من سلايدات الـ PDF التي قام الدكتور بقرائتها أو التركيز عليها. "
    "2. الملاحظات (Sticky Notes): لخص أو استخرج أي شرح إضافي أو أمثلة ذكرها الدكتور بصوته (ولم تكن مكتوبة نصاً في السلايدات)، واذكر رقم الصفحة/الشريحة التي كان يشرحها وقتها. "
    "3. نقاط للتركيز عليها (Focus Points): استخرج أي جمل أو مواضيع أشار الدكتور صراحة إلى أهميتها (مثل: 'هذا مهم جداً'، 'ركزوا هنا'، 'موضع أسئلة') مع شرح مبسط لها. "
    "4. شرح الشرائح (Slide Explanations): قم بشرح محتوى كل شريحة موجودة في الـ PDF شرحاً وافياً ومبسطاً باللغة العربية السهلة (كأنك طالب ممتاز يشرح لزميله) بالاعتماد الأساسي على النص المكتوب في الشريحة نفسها لتوضيح المفاهيم، وإذا ذكر الدكتور تفاصيل إضافية في التسجيل الصوتي تتعلق بهذه الشريحة، قم بدمجها مع الشرح. يجب أن تغطي كافة الشرائح. "
    "5. أسئلة اختبار (MCQs): قم بصياغة 3 أسئلة اختيار من متعدد صعبة ودقيقة فقط من أهم المعلومات لاختبار فهم الطالب. "
    "6. بطاقات حفظ (Flashcards): استخرج أهم 4 مصطلحات أو مفاهيم علمية صعبة فقط، لتستخدم كبطاقات مراجعة سريعة. "
    "يجب أن ترجع المخرجات بصيغة JSON فقط (بدون أي نصوص خارج الـ JSON وبصيغة صحيحة) كالتالي:\n"
    "{{\n"
    '  "highlights": [{{"text_to_highlight": "النص الدقيق من الـ PDF", "page_number": 1}}],\n'
    '  "sticky_notes": [{{"note_text": "الملاحظة الإضافية المختصرة للاستيعاب", "page_number": 1}}],\n'
    '  "focus_points": [{{"point": "موضوع أو نقطة التركيز", "explanation": "سبب الأهمية أو الشرح المستنتج", "page_number": 1}}],\n'
    '  "slide_explanations": [{{"page_number": 1, "explanation": "الشرح المبسط بناءً على تفاصيل السلايدة والصوت."}}],\n'
    '  "mcqs": [{{"question": "السؤال هنا؟", "options": ["خيار1", "خيار2", "خيار3", "خيار4"], "correct_answer": "خيار1", "explanation": "سبب الإجابة الصحيحة"}}],\n'
    '  "flashcards": [{{"front": "المصطلح بالانجليزي أو المفهوم", "back": "الشرح المبسط بالعربي"}}]\n'
    "}}\n"
    "تنبيهات هامة جداً:\n"
    "1. اللغة: يجب أن تكون جميع المخرجات (الملاحظات، الشروحات، الأسئلة) باللغة العربية الفصحى المبسطة فقط. ترجم أي محتوى إنجليزي للعربية فوراً.\n"
    "2. التنسيق: تأكد من أن المخرجات تمثل كائن JSON برمجي صالح 100% (Valid JSON).\n"
    "3. الهروب: يجب عمل Escape لأية علامات تنصيص (\") داخل النصوص باستخدام (\\\").\n"
    "4. الكمية: لا تقم بإرجاع مصفوفات فارغة []، استخرج أكبر قدر ممكن من المعلومات الموثوقة."
)

def _truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, preserving sentence boundaries."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Try to end at a sentence boundary
    last_period = max(truncated.rfind('.'), truncated.rfind('،'), truncated.rfind('\n'))
    if last_period > max_chars * 0.8:  # Only cut at boundary if reasonable
        truncated = truncated[:last_period + 1]
    return truncated + "\n[... تم اختصار النص لأسباب تقنية ...]\n"


def generate_smart_annotations(transcript: str, pdf_context: list[str], user_id: str = None) -> dict:
    """Generates structured JSON annotations (highlights, sticky notes) from transcript and RAG context."""
    from app.core.logger import logger
    llm = get_llm_for_user(user_id)
    if not llm:
        return {"highlights": [], "sticky_notes": []}

    # Default limits for free users (to stay under Groq TPM limits)
    MAX_TRANSCRIPT_CHARS = 3000
    MAX_PDF_CONTEXT_CHARS = 2000
    
    # Check if user is paid to give them full context
    is_paid = False
    if user_id:
        from app.services.subscription_service import get_user_subscription_info
        try:
            sub_info = get_user_subscription_info(user_id)
            if sub_info.get("active_subscription"):
                is_paid = True
        except Exception:
            pass

    if is_paid:
        # Paid users get massive context window (MiniMax supports huge contexts)
        # 250k chars is ~40k words, enough for a 2.5 hour fast-paced lecture.
        MAX_TRANSCRIPT_CHARS = 250000
        MAX_PDF_CONTEXT_CHARS = 150000

    transcript_truncated = _truncate_text(transcript, MAX_TRANSCRIPT_CHARS)
    if len(transcript_truncated) < len(transcript):
        logger.warning(f"Transcript truncated from {len(transcript)} to {len(transcript_truncated)} chars")

    user_msg = ""
    if pdf_context:
        newline = "\n"
        pdf_text = newline.join(pdf_context)
        pdf_text = _truncate_text(pdf_text, MAX_PDF_CONTEXT_CHARS)
        user_msg += f"سياق شرائح الـ PDF المتوفرة (مع أرقام الصفحات):\n{pdf_text}\n\n"
    user_msg += f"النص الصوتي المفرغ:\n{transcript_truncated}\n\nتذكر: أرجع المخرجات ككائن JSON فقط!"

    prompt = ChatPromptTemplate.from_messages([
        ("system", SUMMARY_SYSTEM_PROMPT),
        ("human", "{user_input}")
    ])
    
    chain = prompt | llm
    try:
        result = chain.invoke({"user_input": user_msg})
        content = result.content
        
        # Dump raw output for inspection
        import os
        os.makedirs("logs", exist_ok=True)
        with open("logs/last_raw_gemini.txt", "w", encoding="utf-8") as f:
            f.write(content)
            
        # Safely extract just the JSON dictionary by finding the first { and last }
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        
        if start_idx != -1 and end_idx != -1:
            clean_json = content[start_idx:end_idx+1]
            
            def repair_json(j_str: str) -> str:
                import re
                # 1. Fix missing commas between properties: "value" "next_key" -> "value", "next_key"
                j_str = re.sub(r'("\s*:\s*[^"]*")\s*("\s*)', r'\1, \2', j_str)
                # 2. Fix missing commas between array items: } { -> }, {
                j_str = re.sub(r'}\s*\n*\s*{', '}, {', j_str)
                # 3. Fix missing commas between array items: ] [ -> ], [
                j_str = re.sub(r']\s*\n*\s*\[', '], [', j_str)
                # 4. Fix trailing commas: , } -> }
                j_str = re.sub(r',\s*([\]}])', r'\1', j_str)
                # 5. Fix common missing commas after values: "val" "key" -> "val", "key"
                j_str = re.sub(r'([^\s,\[{])\s*\n*\s*"(?=[^"]*":)', r'\1, "', j_str)
                return j_str

            try:
                # Try standard parsing first with strict=False
                return json.loads(repair_json(clean_json), strict=False)
            except json.JSONDecodeError as e:
                # Second attempt: even more aggressive cleanup
                import re
                aggressive_fix = repair_json(clean_json)
                aggressive_fix = re.sub(r'[\x00-\x1F]+', ' ', aggressive_fix) # Remove control chars
                try:
                    return json.loads(aggressive_fix, strict=False)
                except Exception:
                    logger.error(f"Aggressive JSON fix failed: {e}")
                    raise e
        else:
            from app.core.logger import logger
            logger.error("No JSON object found in Gemini response.")
            raise ValueError("The AI model failed to produce a valid JSON response structure.")
            
    except Exception as e:
        import traceback
        from app.core.logger import logger
        logger.error(f"Error generating annotations JSON: {e}\n{traceback.format_exc()}")
        raise e


def extract_contextual_glossary(transcript: str, user_id: str = None) -> dict:
    """Extracts difficult terms and their contextual Arabic meaning."""
    llm = get_llm_for_user(user_id)
    if not llm:
        return {}

    prompt = ChatPromptTemplate.from_messages([
        ("system", "أنت تستخرج المصطلحات الإنجليزية الصعبة من النص التالي. بدلاً من ترجمتها القياسية، أكتب كيف شرحها الدكتور. أرجع النتيجة فقط بصيغة JSON كالتالي بدون أي نص إضافي: {{\"المصطلح\": \"الشرح السياقي\"}}"),
        ("human", "النص:\n{transcript}")
    ])
    
    chain = prompt | llm
    try:
        result = chain.invoke({"transcript": transcript})
        clean_json = result.content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        print(f"Error parsing glossary JSON: {e}")
        return {}


def detect_emphasis(transcript: str, user_id: str = None) -> list[dict]:
    """Detects parts of the lecture the professor emphasized."""
    llm = get_llm_for_user(user_id)
    if not llm:
        return []

    prompt = ChatPromptTemplate.from_messages([
        ("system", 'ابحث في النص المفرغ التالي عن جمل ركز عليها الدكتور بشكل كبير (مثل: مهم جداً، يأتي بالامتحان). أرجع النتيجة فقط كـ JSON list of dicts، بدون أي نصوص إضافية، بالشكل التالي: [{{"text": "الجملة المستخرجة", "reason": "سبب الأهمية المستنتج"}}]'),
        ("human", "{transcript}")
    ])
    
    chain = prompt | llm
    try:
        result = chain.invoke({"transcript": transcript})
        clean_json = result.content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        print(f"Error parsing emphasis JSON: {e}")
        return []
