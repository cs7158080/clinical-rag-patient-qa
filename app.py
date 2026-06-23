# app.py
import os

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

# Fail fast if Pinecone credentials are missing
if not os.environ.get("PINECONE_API_KEY"):
    raise RuntimeError("PINECONE_API_KEY not set in environment")
if not os.environ.get("PINECONE_INDEX_NAME"):
    raise RuntimeError("PINECONE_INDEX_NAME not set in environment")

from ingest import ingest_folder
from src.pipeline import build_pipeline

pipeline = build_pipeline()

TEMPLATE_OPTIONS = ["הכל", "אבחון", "תוכנית טיפול"]

TEMPLATE_TYPE_MAP = {
    "הכל": None,
    "אבחון": "diagnosis",
    "תוכנית טיפול": "treatment_plan",
}

CUSTOM_CSS = """
body { background: #f1f5f9 !important; }

/* Override all Gradio width wrappers */
gradio-app, gradio-app > .app, gradio-app > .app > .contain,
gradio-app > div, .app.svelte-182fdeq {
    max-width: 100% !important;
    width: 100% !important;
    padding: 0 !important;
}

.gradio-container {
    max-width: 1300px !important;
    width: calc(100% - 48px) !important;
    margin: 36px auto !important;
    background: #ffffff !important;
    border-radius: 20px !important;
    box-shadow: 0 2px 24px rgba(0,0,0,0.08) !important;
    overflow: hidden !important;
    padding: 0 !important;
}

.gradio-container, .gradio-container * {
    direction: rtl !important;
    font-family: 'Segoe UI', Arial, sans-serif !important;
}

/* Header */
.app-header {
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
    padding: 26px 36px 20px 36px;
    text-align: center;
}
.app-header h1 {
    font-size: 1.4rem;
    font-weight: 700;
    color: #1e3a5f;
    margin: 0 0 5px 0;
}
.app-header p {
    color: #64748b;
    font-size: 0.84rem;
    margin: 0;
}

/* Main padding */
#main-content {
    padding: 28px 36px 36px 36px !important;
    gap: 20px !important;
}

/* Radio as chips */
.chip-radio .wrap {
    display: flex !important;
    flex-direction: row !important;
    gap: 8px !important;
    flex-wrap: wrap !important;
}
.chip-radio label {
    cursor: pointer;
    padding: 6px 18px !important;
    border-radius: 20px !important;
    border: 1.5px solid #e2e8f0 !important;
    font-size: 0.85rem !important;
    color: #475569 !important;
    background: #ffffff !important;
    transition: all 0.15s !important;
    user-select: none !important;
}
.chip-radio label:has(input:checked) {
    background: #2563eb !important;
    border-color: #2563eb !important;
    color: #ffffff !important;
    font-weight: 600 !important;
}
.chip-radio input[type="radio"] { display: none !important; }
.chip-radio .svelte-1gfkn6j { display: none !important; }

/* Input row */
#question-input textarea {
    font-size: 0.95rem !important;
    border-radius: 10px !important;
    padding: 10px 14px !important;
}

/* Answer card */
.answer-card {
    background: #f8faff;
    border: 1px solid #dbeafe;
    border-right: 3px solid #3b82f6;
    border-radius: 12px;
    padding: 20px 22px;
    font-size: 1rem;
    line-height: 1.9;
    color: #1e293b;
    white-space: pre-wrap;
    direction: rtl;
}

/* Sources */
.source-row {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.8rem;
    color: #475569;
    padding: 8px 12px;
    border-radius: 8px;
    background: #f8fafc;
    border: 1px solid #f1f5f9;
    margin-bottom: 4px;
    direction: rtl;
}
.src-name { font-weight: 600; color: #1e293b; }
.src-sep { color: #cbd5e1; }
.src-section { color: #64748b; flex: 1; }
.src-score {
    background: #eff6ff;
    color: #2563eb;
    border-radius: 20px;
    padding: 2px 10px;
    font-weight: 600;
    font-size: 0.72rem;
}
.no-sources { color: #94a3b8; font-size: 0.82rem; direction: rtl; padding: 4px 0; }
.sources-count {
    font-size: 0.75rem;
    color: #94a3b8;
    margin-bottom: 10px;
    direction: rtl;
}

footer { display: none !important; }
"""


def pick_folder() -> str:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title="בחר תיקייה לייבוא")
    root.destroy()
    return folder or ""


def run_ingest(folder_path: str) -> str:
    if not folder_path.strip():
        return "יש לבחור תיקייה תחילה."
    try:
        stats = ingest_folder(folder_path)
        pipeline.reload()
        return (
            f"ייבוא הושלם!\n"
            f"קבצים: {stats['files_processed']}  |  "
            f"צ'אנקים: {stats['chunks']}  |  "
            f"דולגו: {stats['skipped']}\n"
            f"נוספו: {stats['added']}  |  "
            f"עודכנו: {stats['updated']}  |  "
            f"ללא שינוי: {stats['unchanged']}"
        )
    except Exception as exc:
        return f"שגיאה בייבוא: {exc}"


def format_sources_html(hits: list) -> str:
    if not hits:
        return "<div class='no-sources'>לא נמצאו מקורות</div>"
    rows = []
    for h in hits:
        score_pct = f"{h['similarity_score']:.0%}"
        client = h.get("client_name") or "לא ידוע"
        section = h.get("section_title", "")
        rows.append(
            f"<div class='source-row'>"
            f"<span class='src-name'>{client}</span>"
            f"<span class='src-sep'>·</span>"
            f"<span class='src-section'>{section}</span>"
            f"<span class='src-score'>{score_pct}</span>"
            f"</div>"
        )
    count = len(hits)
    return f"<div class='sources-count'>{count} מקורות נמצאו</div>" + "".join(rows)


def answer(question: str, template_filter: str):
    if not question.strip():
        return gr.update(visible=False, value=""), ""

    type_filter = TEMPLATE_TYPE_MAP.get(template_filter)
    hits = pipeline.search(question, template_type=type_filter)
    answer_text = pipeline.ask(question, hits)
    return (
        gr.update(visible=True, value=f"<div class='answer-card'>{answer_text}</div>"),
        format_sources_html(hits),
    )


with gr.Blocks(
    title="מערכת שאלות על קבצי לקוחות",
    theme=gr.themes.Default(primary_hue="blue", neutral_hue="slate"),
    css=CUSTOM_CSS,
) as demo:

    gr.HTML("""
    <div class="app-header">
        <h1>🩺 מערכת שאלות על קבצי לקוחות</h1>
        <p>שאלי שאלות על אבחונים ותוכניות טיפול של לקוחות</p>
    </div>
    """)

    with gr.Column(elem_id="main-content"):

        template_filter = gr.Radio(
            choices=TEMPLATE_OPTIONS,
            value="הכל",
            label="סוג מסמך",
            elem_classes="chip-radio",
        )

        with gr.Row():
            question = gr.Textbox(
                label="",
                placeholder="לדוגמה: מה הקשיים של יוסי? מה מטרות הטיפול של מרים?",
                lines=5,
                scale=5,
                container=False,
                elem_id="question-input",
            )
            submit = gr.Button("🔍 חפש", variant="primary", scale=1, min_width=110)

        answer_display = gr.HTML(visible=False)

        with gr.Accordion("📄 מקורות", open=False):
            sources_display = gr.HTML()

        with gr.Accordion("📁 ייבוא מסמכים", open=False):
            with gr.Row():
                folder_path_box = gr.Textbox(
                    label="תיקייה נבחרת",
                    placeholder="לחצי על 'בחר תיקייה'...",
                    interactive=False,
                    scale=5,
                )
                pick_btn = gr.Button("📂 בחר תיקייה", scale=1, min_width=130)
            import_btn = gr.Button("📥 ייבא מסמכים", variant="primary")
            ingest_status = gr.Textbox(label="סטטוס", lines=3, interactive=False)

            pick_btn.click(fn=pick_folder, outputs=folder_path_box)
            import_btn.click(
                fn=lambda: gr.update(value="⏳ מייבא...", interactive=False),
                outputs=import_btn,
            ).then(
                fn=run_ingest,
                inputs=folder_path_box,
                outputs=ingest_status,
            ).then(
                fn=lambda: gr.update(value="📥 ייבא מסמכים", interactive=True),
                outputs=import_btn,
            )

    for trigger in [submit.click, question.submit]:
        trigger(
            fn=lambda: gr.update(value="⏳ מחפש...", interactive=False),
            outputs=submit,
        ).then(
            fn=answer,
            inputs=[question, template_filter],
            outputs=[answer_display, sources_display],
        ).then(
            fn=lambda: gr.update(value="🔍 חפש", interactive=True),
            outputs=submit,
        )

demo.launch()
