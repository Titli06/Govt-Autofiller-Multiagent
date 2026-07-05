"""Form model: an uploaded form + its filled fields, confidence, and review state.

Persists per-field provenance and confidence so every auto-filled value is auditable.
"""

# TODO: Form(id, user_id, form_type, status) ; FormField(form_id, name, value,
#       source_doc_id, confidence, needs_review, reviewed)
