"""Profile model: the verified personal data store.

Field values are stored field-level encrypted (see core/encryption.py). JSONB holds
the flexible per-field structure; each field references the source document it came from.
"""

# TODO: Profile(id, user_id) ; ProfileField(profile_id, name, value_encrypted, source_doc_id)
