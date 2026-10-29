import os
import fitz
from app.services.pdf_annotator import annotate_pdf

def test_full_pipeline():
    # 1. Create a dummy input PDF with 2 slides
    dummy_input = "test_input_deck.pdf"
    dummy_output = "test_output_annotated.pdf"
    
    doc = fitz.open()
    # Slide 1: Title slide
    s1 = doc.new_page(width=842, height=595)
    s1.insert_textbox(fitz.Rect(50, 50, 792, 100), "Blockchain Fundamentals - Lecture 1", fontsize=24)
    
    # Slide 2: Centralization slide
    s2 = doc.new_page(width=842, height=595)
    s2.insert_textbox(fitz.Rect(50, 50, 792, 100), "Slide 2: The idea of centralization", fontsize=24)
    s2.insert_textbox(fitz.Rect(50, 150, 792, 400), "Centralization is pervasive in modern institutions.\nSingle Point of Failure risks.", fontsize=14)
    
    doc.save(dummy_input)
    doc.close()
    
    # 2. Annotations data simulating tutor agent output
    annotations = {
        "highlights": [
            {"page_number": 2, "text_to_highlight": "Single Point of Failure", "color": "yellow"}
        ],
        "slide_explanations": [
            {
                "page_number": 2,
                "title": "The idea of centralization - مفهوم المركزية ومخاطرها",
                "summary_phrase": "فهم كيف تعمل الأنظمة المركزية ولماذا تشكل نقطة ضعف أمنية واقتصادية",
                "doctor_explanation": (
                    "• ركز الدكتور بشكل أساسي على فكرة أن المركزية تعني وجود جهة واحدة تتحكم في كل البيانات والقرارات.\n"
                    "• ضرب مثالاً واضحاً عن البنوك: لو حصل اختراق لقاعدة بيانات البنك، كل بيانات العملاء بتتسرب.\n"
                    "• نبّه الدكتور إلى أن المركزية ليست سيئة بالمطلق بل لها إيجابيات تنظيمية ولكن مخاطرها الأمنية عالية جداً."
                ),
                "ai_explanation": (
                    "المركزية (Centralization) ببساطة هي نمط تنظيمي يعتمد على حصر كافة الصلاحيات وإدارة البيانات وقواعد اتخاذ القرار داخل خادم واحد أو جهة محددة.\n"
                    "في هذا النموذج، لا يمتلك المستخدمون أي سلطة على بياناتهم، بل يمنحون 'ثقة عمياء' (Blind Trust) للسلطة المركزية لإدارة سجلاتهم وحساباتهم.\n"
                    "تكمن المعضلة الكبرى في ما يسمى 'نقطة الفشل الفردية' (Single Point of Failure): إذا انهار الخادم المركزي يسقط النظام بالكامل."
                ),
                "example": (
                    "تخيل لو في مدينة كاملة ما فيها غير محطة بنزين واحدة مركزية بتغذي كل السيارات.\n"
                    "طول ما المحطة شغالة الأمور تمام، لكن لو تعطلت مضخة واحدة أو شب حريق، المدينة كلها بتوقف فجأة وتتشل حركتها!\n"
                    "هذا بالضبط هو مفهوم 'نقطة الفشل الفردية' (Single Point of Failure) في الأنظمة المركزية."
                ),
                "exam_tip": (
                    "فخ امتحاني حاسم: انتبه جيداً للفرق بين مصطلحي 'Governance' و 'Centralization'.\n"
                    "الـ Governance هو المبدأ العام لحوكمة وإدارة القوة والتنظيم، أما الـ Centralization فهو مجرد أحد أشكال الـ Governance وليس مرادفاً له!"
                ),
                "vocabulary": [
                    {"term": "Centralization", "translation": "المركزية", "simple_def": "التحكم عبر سلطة أو جهة واحدة"},
                    {"term": "Single Point of Failure (SPOF)", "translation": "نقطة الفشل الفردية", "simple_def": "عنصر مركزي يؤدي تعطله لانهيار النظام بالكامل"}
                ]
            }
        ]
    }
    
    out_path = annotate_pdf(dummy_input, dummy_output, annotations)
    print(f"Output saved to: {out_path}")
    
    # 3. Verify generated PDF
    out_doc = fitz.open(out_path)
    print(f"Total pages in annotated PDF: {len(out_doc)}")
    print(f"slide_page_map: {annotations.get('slide_page_map')}")
    print(f"companion_page_map: {annotations.get('companion_page_map')}")
    
    # Slide 1 had no explanation -> 1 page
    # Slide 2 had doctor audio, core AI breakdown, example, and vocab/exam -> 1 slide + 2 companion pages (Part 1 & Part 2) = 3 pages for slide 2!
    # Total doc length should be 1 + 3 = 4 pages!
    assert len(out_doc) == 4, f"Expected 4 pages, got {len(out_doc)}"
    comp_pages = annotations["companion_page_map"][2]
    assert len(comp_pages) == 2, f"Expected 2 companion pages for slide 2, got {len(comp_pages)}"
    print(f"All assertions PASSED! Companion pages: {comp_pages}")
    
    # Clean up
    out_doc.close()
    if os.path.exists(dummy_input): os.remove(dummy_input)
    if os.path.exists(dummy_output): os.remove(dummy_output)

if __name__ == "__main__":
    test_full_pipeline()
