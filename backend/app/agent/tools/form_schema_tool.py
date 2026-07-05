"""form_schema_tool — identify the form type and its required fields.

Uses a known template from app/templates/ when the form is recognized. When it is
not, infers the field schema from the uploaded form itself (UC3/FR4 — the hardest,
most differentiating path). Inferred schemas default to lower confidence downstream.
"""

# TODO: identify_or_infer_schema(form_document) -> {form_type, required_fields}
