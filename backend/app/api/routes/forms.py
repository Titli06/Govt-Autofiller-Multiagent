"""Form routes: upload a blank form, run the fill pipeline, review flagged fields, download.

Download is BLOCKED until every flagged field has been reviewed. No route ever submits
the form to an external portal.
"""

from fastapi import APIRouter

router = APIRouter()

# TODO: POST /upload, GET /{id}/review, POST /{id}/review (approve/correct), GET /{id}/download
