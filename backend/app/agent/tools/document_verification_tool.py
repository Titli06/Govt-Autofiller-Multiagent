"""document_verification_tool — cross-check each candidate value against its source doc.

This is the trust layer that prevents silent drift between profile data and the
finalized form. An exact match to the original document is the strongest signal the
confidence scorer has; never treat an unverified value as high-confidence.
"""

# TODO: verify(candidates) -> annotate each with verified: bool + evidence
