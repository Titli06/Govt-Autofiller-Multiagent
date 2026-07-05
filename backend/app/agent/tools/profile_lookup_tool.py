"""profile_lookup_tool — map form fields to stored profile values.

Handles phrasing/format variance semantically ("Father's Name" vs "Name of Father",
DD/MM/YYYY vs YYYY-MM-DD). Returns candidate values with the source document id that
backs each one, for downstream verification and auditability.
"""

# TODO: lookup(required_fields, user_id) -> list[candidate_value_with_provenance]
