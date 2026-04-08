from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import ChatPromptTemplate
from app.core.config import settings
import json

# Initialize Gemini model
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    google_api_key=settings.GEMINI_API_KEY, 
    temperature=0.2
) if settings.GEMINI_API_KEY else None

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
    "هام جداً: \n"
    "- تأكد من أن المخرجات تمثل كائن JSON برمجي صالح 100% (Valid JSON).\n"
    "- لا تقم بإرجاع مصفوفات فارغة [] أبداً! ابذل أقصى جهدك لاستخراج أكبر كمية ممكنة من التظليلات والملاحظات حتى لو بشكل تقريبي.\n"
    "- يجب عمل Escape لأية علامات تنصيص (\") داخل النصوص باستخدام (\\\")."
)

def generate_smart_annotations(transcript: str, pdf_context: list[str]) -> dict:
    """Generates structured JSON annotations (highlights, sticky notes) from transcript and RAG context."""
    if not llm:
        return {"highlights": [], "sticky_notes": []}

    user_msg = ""
    if pdf_context:
        newline = "\n"
        user_msg += f"سياق شرائح الـ PDF المتوفرة (مع أرقام الصفحات):\n{newline.join(pdf_context)}\n\n"
    user_msg += f"النص الصوتي المفرغ:\n{transcript}\n\nتذكر: أرجع المخرجات ككائن JSON فقط!"

    prompt = ChatPromptTemplate.from_messages([
        ("system", SUMMARY_SYSTEM_PROMPT),
        ("human", "{user_input}")
    ])
    
    chain = prompt | llm
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
            try:
                # strict=False allows literal newlines and control characters inside JSON strings
                return json.loads(clean_json, strict=False)
            except json.JSONDecodeError:
                import re
                # If it still fails, aggressively strip control characters
                clean_json = re.sub(r'[\x00-\x1F]+', ' ', clean_json)
                return json.loads(clean_json, strict=False)
        else:
            from app.core.logger import logger
            logger.error("No JSON object found in Gemini response.")
            raise ValueError("The AI model failed to produce a valid JSON response structure.")
            
    except Exception as e:
        import traceback
        from app.core.logger import logger
        logger.error(f"Error generating annotations JSON: {e}\n{traceback.format_exc()}")
        raise e


def extract_contextual_glossary(transcript: str) -> dict:
    """Extracts difficult terms and their contextual Arabic meaning."""
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


def detect_emphasis(transcript: str) -> list[dict]:
    """Detects parts of the lecture the professor emphasized."""
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
