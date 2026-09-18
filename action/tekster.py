"""
De faste tekster, ManiLens skriver på GitHub, på dansk og engelsk.

Sproget kommer fra kollegaens konto: brokeren sender det i admit-svaret, workflowet
lægger det i MANILENS_LANGUAGE, og modellen får det samme sprog i prompten.

VIGTIGT: kun tekst, mennesker læser, står her. Nøglerne i motorens JSON-skema er
danske ord (`kritisk|alvorlig|mindre`, `tilfoejet|aendret|fjernet`) og bruges som
wire-format mellem prompt, Python og websiden — og de ligger gemt i review-oversigter.
De må aldrig oversættes; kun deres etiketter.
"""
import os

DEFAULT = "da"

SPROG = ("da", "en")

DA = {
    "severity_kritisk": "🔴 Kritisk",
    "severity_alvorlig": "🟠 Alvorlig",
    "severity_mindre": "🟡 Mindre",
    "category_default": "Korrekthed",

    "risk_high": "🔴 Høj",
    "risk_high_why": "Der er alvorlige eller kritiske fund, som skal afklares før merge.",
    "risk_unclear": "⚪ Uafklaret",
    "risk_unclear_why": "Reviewet blokerer: tjek eller tidligere fund er ikke afklaret.",
    "risk_moderate": "🟡 Moderat",
    "risk_moderate_why": "Der er mindre fund, men ingen registrerede blokerende fund.",
    "risk_low": "🟢 Lav",
    "risk_low_why": "Ingen blokerende fund i dette review; det er ikke en garanti for fejlfri kode.",

    "agent_prompt_summary": "🤖 Samlet fix-prompt til AI-agenter",
    "agent_prompt_single": "🤖 Prompt til AI-agenter",

    "checks_header_check": "Tjek",
    "checks_header_status": "Status",
    "checks_header_why": "Forklaring",
    "checks_mode_error": "blokerer",
    "checks_mode_warning": "advarsel",

    "review_blocked": "ManiLens har fundet noget, der skal rettes før merge.",
    "review_approved": "ManiLens godkender: ingen blokerende fund.",
    "review_counts": "{kritisk} kritiske · {alvorlig} alvorlige · {mindre} mindre · {fixed} rettet siden sidst",
    "outside_heading": "### Fund uden for de ændrede linjer",
    "premerge_heading": "### Pre-merge checks",
    "review_info": "ℹ️ Review info",
    "review_info_commit": "- Commit: `{head}`",
    "review_info_inline": "- Nye linjekommentarer: {count}",
    "review_info_files": "- Ændrede filer i PR'en (ikke en garanti for komplet analyse):",

    "open_snapshot": "Åbn review-oversigten",
    "snapshot_failed": "_Review-oversigten kunne ikke gemmes._",
    "open_workspace": "Åbn review-workspace (download manilens-workspace)",
    "merge_risk": "**Merge-risiko: {risk}** · commit `{head}`",
    "walkthrough_summary": "Gennemgang af ændringerne",
    "walkthrough_files": "Filer",
    "walkthrough_change": "Ændring",
    "sequence_diagram": "Sekvensdiagram",
    "rejected_summary": "{count} mulige fund blev afvist ved efterprøvning",
    "finishing_summary": "✨ Finishing Touches",
    "finishing_intro": "Vælg én handling. Den bruger Claude-kvote og afleverer en ny draft-PR.",
    "premerge_summary": "Pre-merge checks · ✅ {passed} · ❌ {failed}",
    "commands_hint": "Brug `@manilens fix prompt` til en samlet prompt for åbne linjefund, eller `@manilens help` for kommandoer.",

    "summary_tilfoejet": "Tilføjet",
    "summary_aendret": "Ændret",
    "summary_fjernet": "Fjernet",
    "summary_heading": "## Opsummering fra ManiLens",

    "no_finishing": ("Kodehandlinger er ikke tilgængelige for kolleger i v1. "
                     "Brug `@manilens fix prompt` for en samlet prompt til din egen coding-agent; ingen Claude-kald."),
    "no_open_findings": "Ingen åbne linjefund fra ManiLens.",
    "finishing_not_enabled": ("Kodehandlinger er ikke aktiveret. Repoets ejer skal først kontrollere forbrugsindstillinger "
                              "og slå dem til."),
    "paused": "⏸️ ManiLens holder pause på denne PR.",
    "resumed": "▶️ ManiLens reviewer igen fra næste push (eller `@manilens review`).",
    "learning_suggestion": "📝 Forslag til `.manilens/laering.md`: {learning}",
    "blocking_stays_open": "_Fundet er blokerende og lukkes først, når et nyt review bekræfter rettelsen._",

    "help": """## ManiLens-kommandoer

| Kommando | Handling |
|---|---|
| `@manilens review` | Review med tidligere fund som kontekst; bruger Claude-kvote |
| `@manilens full review` | Nyt review uden tidligere fund som kontekst; bruger Claude-kvote |
| `@manilens pause` | Stop automatiske reviews på denne PR |
| `@manilens resume` | Genoptag fra næste push |
| `@manilens fix prompt` | Saml åbne linjefund til din coding-agent; ingen Claude-kald |
| `@manilens help` | Vis denne hjælp; ingen Claude-kald |

Du kan også stille spørgsmål med `@manilens` eller svare i en fundtråd.
""",

    "review_failed": ("⚠️ **Review kunne ikke gennemføres for commit {head}.** "
                      "PR'en er ikke godkendt. Se kørslen: {run_url}"),

    "below_threshold": "under dommerens grænse (confidence {value} < {minimum})",
    "confidence_missing": "under dommerens grænse (confidence mangler eller er ugyldig, kræver >= {minimum})",

    "prompt_language": "Danish",
}

EN = {
    "severity_kritisk": "🔴 Critical",
    "severity_alvorlig": "🟠 Major",
    "severity_mindre": "🟡 Minor",
    "category_default": "Correctness",

    "risk_high": "🔴 High",
    "risk_high_why": "There are major or critical findings that need to be resolved before merging.",
    "risk_unclear": "⚪ Unresolved",
    "risk_unclear_why": "The review blocks: checks or earlier findings are unresolved.",
    "risk_moderate": "🟡 Moderate",
    "risk_moderate_why": "There are minor findings, but no recorded blocking findings.",
    "risk_low": "🟢 Low",
    "risk_low_why": "No blocking findings in this review; that is not a guarantee of flawless code.",

    "agent_prompt_summary": "🤖 Combined fix prompt for AI agents",
    "agent_prompt_single": "🤖 Prompt for AI agents",

    "checks_header_check": "Check",
    "checks_header_status": "Status",
    "checks_header_why": "Explanation",
    "checks_mode_error": "blocks",
    "checks_mode_warning": "warning",

    "review_blocked": "ManiLens found something that needs fixing before merge.",
    "review_approved": "ManiLens approves: no blocking findings.",
    "review_counts": "{kritisk} critical · {alvorlig} major · {mindre} minor · {fixed} fixed since last time",
    "outside_heading": "### Findings outside the changed lines",
    "premerge_heading": "### Pre-merge checks",
    "review_info": "ℹ️ Review info",
    "review_info_commit": "- Commit: `{head}`",
    "review_info_inline": "- New line comments: {count}",
    "review_info_files": "- Files changed in the PR (not a guarantee of complete analysis):",

    "open_snapshot": "Open the review overview",
    "snapshot_failed": "_The review overview could not be saved._",
    "open_workspace": "Open review workspace (download manilens-workspace)",
    "merge_risk": "**Merge risk: {risk}** · commit `{head}`",
    "walkthrough_summary": "Walkthrough of the changes",
    "walkthrough_files": "Files",
    "walkthrough_change": "Change",
    "sequence_diagram": "Sequence diagram",
    "rejected_summary": "{count} possible findings were rejected during verification",
    "finishing_summary": "✨ Finishing Touches",
    "finishing_intro": "Pick one action. It uses Claude quota and delivers a new draft PR.",
    "premerge_summary": "Pre-merge checks · ✅ {passed} · ❌ {failed}",
    "commands_hint": "Use `@manilens fix prompt` for a combined prompt covering open line findings, or `@manilens help` for commands.",

    "summary_tilfoejet": "Added",
    "summary_aendret": "Changed",
    "summary_fjernet": "Removed",
    "summary_heading": "## Summary from ManiLens",

    "no_finishing": ("Code actions are not available to colleagues in v1. "
                     "Use `@manilens fix prompt` for a combined prompt for your own coding agent; no Claude calls."),
    "no_open_findings": "No open line findings from ManiLens.",
    "finishing_not_enabled": ("Code actions are not enabled. The repository owner must review the usage settings "
                              "and turn them on first."),
    "paused": "⏸️ ManiLens is paused on this PR.",
    "resumed": "▶️ ManiLens will review again from the next push (or `@manilens review`).",
    "learning_suggestion": "📝 Suggestion for `.manilens/laering.md`: {learning}",
    "blocking_stays_open": "_This finding blocks and stays open until a new review confirms the fix._",

    "help": """## ManiLens commands

| Command | Action |
|---|---|
| `@manilens review` | Review with earlier findings as context; uses Claude quota |
| `@manilens full review` | Fresh review without earlier findings as context; uses Claude quota |
| `@manilens pause` | Stop automatic reviews on this PR |
| `@manilens resume` | Resume from the next push |
| `@manilens fix prompt` | Collect open line findings for your coding agent; no Claude calls |
| `@manilens help` | Show this help; no Claude calls |

You can also ask questions with `@manilens` or reply in a finding thread.
""",

    "review_failed": ("⚠️ **The review could not be completed for commit {head}.** "
                      "The PR is not approved. See the run: {run_url}"),

    "below_threshold": "below the judge's threshold (confidence {value} < {minimum})",
    "confidence_missing": "below the judge's threshold (confidence missing or invalid, requires >= {minimum})",

    "prompt_language": "English",
}

TEKSTER = {"da": DA, "en": EN}


def language():
    """Sproget for denne kørsel. En ukendt værdi falder tilbage til dansk."""
    code = (os.environ.get("MANILENS_LANGUAGE") or "").strip().lower()
    return code if code in TEKSTER else DEFAULT


def t(key, **kwargs):
    """
    Teksten for `key` på kørslens sprog. Mangler nøglen på det valgte sprog, bruges
    dansk — en manglende oversættelse må aldrig give en tom kommentar på GitHub.
    """
    table = TEKSTER[language()]
    text = table.get(key, DA.get(key, key))
    return text.format(**kwargs) if kwargs else text
