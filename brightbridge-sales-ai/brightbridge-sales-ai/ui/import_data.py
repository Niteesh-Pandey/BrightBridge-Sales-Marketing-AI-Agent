"""Import Data tab.

Primary path: upload ALL CSV/XLSX files at once. Secondary: one flat sheet. Both use the same engine:
classify by content → fuzzy header mapping → dependency-ordered validation with automatic foreign-key fallbacks →
report → duplicate mode (SKIP / UPSERT / OVERWRITE) → confirm → atomic import.
"""
from __future__ import annotations

import gradio as gr
import pandas as pd

import config
from services import bulk_import_service as bi
from services import import_service as imp
from ui.common import Refreshable, safe

OVERWRITE_WORD = "OVERWRITE"


def _stats_text(stats: dict) -> str:
    parts = []
    for t in bi.IMPORT_ORDER:
        if stats.get(t):
            parts.append(f"{stats[t]} new {t}")
        if stats.get(f"{t}_updated"):
            parts.append(f"{stats[f'{t}_updated']} {t} updated")
    return ", ".join(parts) or "nothing new"


def _notes_markdown(res: bi.BundleResult) -> str:
    out = []
    if res.notices:
        out.append("**ℹ️ Notices** (informational — nothing was skipped)\n" + "\n".join(f"- {n}" for n in res.notices[:20])
                   + (f"\n- … and {len(res.notices) - 20} more (see report)" if len(res.notices) > 20 else ""))
    if res.warnings:
        out.append("**⚠️ Warnings**\n" + "\n".join(f"- {w}" for w in res.warnings[:20]) + (f"\n- … and {len(res.warnings) - 20} more (see report)" if len(res.warnings) > 20 else ""))
    return "\n\n".join(out)


def _wipe_update(wipe: pd.DataFrame):
    return gr.update(value=wipe, visible=not wipe.empty)


def _headline(ctx, res: bi.BundleResult) -> str:
    text = f"**{res.headline()}**"
    if not res.ok:
        if res.duplicates and res.mode == "SKIP":
            text += ("\n\n💡 **Everything in these files already exists**, so there is nothing new to import. "
                     "Choose **UPSERT** (update the existing records) or **OVERWRITE** (replace them) above and press *Check* again — "
                     "or press **Reset validation & start fresh**.")
        elif res.errors:
            text += "\n\n💡 No row passed validation. Read the *Errors* table below — it names the file, row and problem."
    if ctx.mode != "real":
        text += "\n\n⚠️ You are in **Demo mode** — this is a preview only. Switch to Real mode to import."
    return text


def build(ctx):
    """Returns (Refreshable, import_events, switch_button, switch_handler)."""
    gr.Markdown("### Import your real business data")
    gr.Markdown(bi.requirements_markdown())
    with gr.Row():
        tpl_btn = gr.Button("⬇ Download ALL templates + guide (ZIP)", scale=1)
        tpl_file = gr.File(label="Templates (ZIP)", interactive=False, scale=2)
    mode_note = gr.Markdown()
    switch_btn = gr.Button("Switch to Real mode now", variant="secondary", visible=False)

    gr.Markdown("#### When a record already exists…")
    mode = gr.Radio([(f"{m} — {bi.MODE_HELP[m].split(' (')[0].split(';')[0]}", m) for m in bi.MODES], value="SKIP", label="Duplicate handling")
    mode_help = gr.Markdown(f"**SKIP:** {bi.MODE_HELP['SKIP']}")
    overwrite_word = gr.Textbox(label=f"Type {OVERWRITE_WORD} to confirm you want existing data replaced", placeholder=OVERWRITE_WORD, visible=False)

    def on_mode(m):
        extra = "\n\n⚠️ **Overwriting a parent table also removes its children** (companies → contacts, leads, deals, activities; leads → deals, activities). " \
                "A backup ZIP is saved first." if m == "OVERWRITE" else ""
        return f"**{m}:** {bi.MODE_HELP[m]}{extra}", gr.update(visible=m == "OVERWRITE", value="")
    mode.change(on_mode, mode, [mode_help, overwrite_word])

    @safe("Template")
    def make_templates():
        return bi.templates_zip()
    tpl_btn.click(make_templates, None, tpl_file)

    def do_import(res, ok, mode_now, word):
        """Shared guard rails for both import paths. Returns (message, backup_path)."""
        if ctx.mode != "real":
            return "⚠️ Switch to **Real mode** before importing. Demo data is never modified.", None
        if res is None or (not res.ok and not res.wiped):
            return "Nothing to import. Upload files and press **Check & validate** first. If everything was a duplicate, choose UPSERT or OVERWRITE, or press *Reset validation*.", None
        if res.mode != mode_now:
            return f"The duplicate mode was changed from {res.mode} to {mode_now} after checking — press **Check & validate** again.", None
        if not ok:
            return "Please tick the confirmation box first.", None
        if res.mode == "OVERWRITE" and (word or "").strip() != OVERWRITE_WORD:
            return f"OVERWRITE deletes existing data. Type **{OVERWRITE_WORD}** in the confirmation box to continue.", None
        stats = bi.import_bundle(ctx.require_db(), res)
        msg = f"✅ Import complete ({res.mode}): {_stats_text(stats)}. Dashboard, CRM and AI Assistant now use your data."
        if stats.get("backup"):
            msg += " A backup of the replaced data was saved (download below)."
        return msg, stats.get("backup")

    events = []
    with gr.Tabs():
        # ------------------------------------------------------------------ multi-file
        with gr.Tab("📦 Upload all files at once (recommended)"):
            gr.Markdown("Select **all** your files together (Ctrl+click / Shift+click). They are recognised from their **content**, not their name, "
                        f"and linked by company. Max {config.MAX_UPLOAD_MB} MB per file.")
            files = gr.File(label="1 · Upload all CSV / XLSX files", file_count="multiple", file_types=[".csv", ".xlsx"], type="filepath")
            with gr.Row():
                check_btn = gr.Button("2 · Check & validate files", variant="primary", scale=3)
                reset_btn = gr.Button("↺ Reset validation & start fresh", scale=1)
            detect_df = gr.Dataframe(label="What I found in each file", interactive=False, wrap=True, max_height=260)
            checklist = gr.Markdown()
            headline = gr.Markdown()
            summary_df = gr.Dataframe(label="Result per file", interactive=False, wrap=True, max_height=240)
            notes_md = gr.Markdown()
            errors_df = gr.Dataframe(label="Errors (these rows are skipped)", interactive=False, wrap=True, max_height=260)
            dups_df = gr.Dataframe(label="Duplicates", interactive=False, wrap=True, max_height=200)
            wipe_df = gr.Dataframe(label="⚠️ OVERWRITE will delete these rows first", interactive=False, max_height=200, visible=False)
            report = gr.File(label="Download full report (errors, duplicates, warnings, notices)", interactive=False)
            confirm = gr.Checkbox(label="I reviewed the report and want to import", value=False)
            import_btn = gr.Button("3 · Import", variant="stop")
            result = gr.Markdown()
            backup_file = gr.File(label="Backup of replaced data (OVERWRITE only)", interactive=False, visible=False)
            bstate = gr.State(None)

            @safe("Check files", 10)
            def on_check(paths, mode_now):
                hide = gr.update(value=None, visible=False)
                blank = lambda msg: (pd.DataFrame(), "", msg, pd.DataFrame(), "", pd.DataFrame(), pd.DataFrame(), hide, None, None)  # noqa: E731
                if not paths:
                    return blank("Please upload at least one file first.")
                try:
                    infos = bi.analyse(paths)
                except imp.ImportError_ as e:
                    return blank(f"⚠️ {e}")
                db = ctx.db if ctx.mode == "real" else None
                res = bi.validate_bundle(infos, db, mode=mode_now)
                wipe = bi.wipe_preview(db, res) if (db is not None and res.wiped) else pd.DataFrame()
                return (bi.detection_table(infos), bi.checklist_markdown(infos), _headline(ctx, res), res.summary_df(), _notes_markdown(res),
                        res.errors_df().head(500), res.duplicates_df().head(500), _wipe_update(wipe), bi.report_csv(res), res)

            def on_reset():
                info = bi.reset_import_session()
                note = "↺ Validation reset. Cached results and temporary report files were cleared — upload your files again. Your data was not changed."
                return (None, pd.DataFrame(), "", "", pd.DataFrame(), "", pd.DataFrame(), pd.DataFrame(), gr.update(value=None, visible=False), None, False, "", note, None,
                        gr.update(value=None, visible=False))

            @safe("Import", 2)
            def on_import(res, ok, mode_now, word):
                msg, backup = do_import(res, ok, mode_now, word)
                return msg, gr.update(value=backup, visible=bool(backup))

            check_btn.click(on_check, [files, mode], [detect_df, checklist, headline, summary_df, notes_md, errors_df, dups_df, wipe_df, report, bstate])
            reset_btn.click(on_reset, None, [files, detect_df, checklist, headline, summary_df, notes_md, errors_df, dups_df, wipe_df, report, confirm, overwrite_word,
                                             result, bstate, backup_file])
            events.append(import_btn.click(on_import, [bstate, confirm, mode, overwrite_word], [result, backup_file]))

        # ------------------------------------------------------------------ single flat file
        with gr.Tab("📄 One flat file (quick)"):
            gr.Markdown("Everything in **one sheet** (one row per lead: company, contact, deal stage/value, last contact date). "
                        "It is split into companies, contacts, leads, deals and activities and uses the same checks and duplicate modes.")
            upload = gr.File(label="1 · Upload file", file_types=[".csv", ".xlsx"], type="filepath")
            preview = gr.Dataframe(label="2 · Preview (first 10 rows)", interactive=False, max_height=240)
            gr.Markdown("**3 · Column mapping** — auto-detected; adjust if needed. Only *Company name* is required.")
            drops, keys = {}, list(imp.FIELDS)
            for i in range(0, len(keys), 3):
                with gr.Row():
                    for k in keys[i:i + 3]:
                        label, req, _ = imp.FIELDS[k]
                        drops[k] = gr.Dropdown(choices=[], value=None, label=label + (" *" if req else ""))
            with gr.Row():
                validate_btn = gr.Button("4 · Validate", variant="primary", scale=3)
                reset_btn1 = gr.Button("↺ Reset validation & start fresh", scale=1)
            summary = gr.Markdown()
            notes1 = gr.Markdown()
            errors = gr.Dataframe(label="Errors (rows are skipped)", interactive=False, max_height=220)
            dups = gr.Dataframe(label="Duplicates", interactive=False, max_height=180)
            wipe1 = gr.Dataframe(label="⚠️ OVERWRITE will delete these rows first", interactive=False, max_height=180, visible=False)
            report1 = gr.File(label="Download report", interactive=False)
            confirm1 = gr.Checkbox(label="I reviewed the validation report and want to import", value=False)
            import_btn1 = gr.Button("5 · Import", variant="stop")
            result1 = gr.Markdown()
            backup1 = gr.File(label="Backup of replaced data (OVERWRITE only)", interactive=False, visible=False)
            state1 = gr.State(None)
            drop_list = [drops[k] for k in keys]

            @safe("Import", 2 + len(keys))
            def on_upload(path):
                blank = [gr.update(choices=[], value=None) for _ in keys]
                if not path:
                    return (pd.DataFrame(), "", *blank)
                try:
                    df = imp.read_file(path)
                except imp.ImportError_ as e:
                    gr.Warning(str(e))
                    return (pd.DataFrame(), f"⚠️ {e}", *blank)
                det = imp.detect_columns(list(df.columns))
                return (imp.preview(df), f"File read: {len(df)} rows, {len(df.columns)} columns.", *[gr.update(choices=list(df.columns), value=det[k]) for k in keys])

            @safe("Validate", 7)
            def on_validate(path, mode_now, *mapped):
                none = lambda msg: (msg, "", pd.DataFrame(), pd.DataFrame(), gr.update(value=None, visible=False), None, None)  # noqa: E731
                if not path:
                    return none("Please upload a file first.")
                try:
                    df = imp.read_file(path)
                    infos = bi.flat_to_infos(df, dict(zip(keys, mapped)))
                    db = ctx.db if ctx.mode == "real" else None
                    res = bi.validate_bundle(infos, db, mode=mode_now)
                except imp.ImportError_ as e:
                    return none(f"⚠️ {e}")
                wipe = bi.wipe_preview(db, res) if (db is not None and res.wiped) else pd.DataFrame()
                return _headline(ctx, res), _notes_markdown(res), res.errors_df().head(500), res.duplicates_df().head(500), _wipe_update(wipe), bi.report_csv(res), res

            def on_reset1():
                bi.reset_import_session()
                note = "↺ Validation reset. Cached results and temporary report files were cleared. Your data was not changed."
                return (None, pd.DataFrame(), "", "", pd.DataFrame(), pd.DataFrame(), gr.update(value=None, visible=False), None, False, note, None,
                        gr.update(value=None, visible=False), *[gr.update(choices=[], value=None) for _ in keys])

            @safe("Import", 2)
            def on_import1(res, ok, mode_now, word):
                msg, backup = do_import(res, ok, mode_now, word)
                return msg, gr.update(value=backup, visible=bool(backup))

            upload.change(on_upload, upload, [preview, summary, *drop_list])
            validate_btn.click(on_validate, [upload, mode, *drop_list], [summary, notes1, errors, dups, wipe1, report1, state1])
            reset_btn1.click(on_reset1, None, [upload, preview, summary, notes1, errors, dups, wipe1, report1, confirm1, result1, state1, backup1, *drop_list])
            events.append(import_btn1.click(on_import1, [state1, confirm1, mode, overwrite_word], [result1, backup1]))

    def refresh():
        if ctx.mode == "real":
            return "✅ **Real mode** is active — imports will be saved to your real-data workspace.", gr.update(visible=False)
        return ("⚠️ You are in **Demo mode**: imports are disabled to protect the demo data. Click the button below to switch to Real mode.", gr.update(visible=True))

    def switch_real():
        ctx.activate("real")

    return Refreshable(refresh, [mode_note, switch_btn]), events, switch_btn, switch_real
