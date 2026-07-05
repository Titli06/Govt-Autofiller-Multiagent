"""Pipeline metrics — these are part of the deliverable, not optional telemetry.

Track: end-to-end latency (upload -> filled form ready), % fields auto-filled at
high confidence, auto-fill accuracy vs. ground truth, time saved per form, and
schema-inference success rate on unseen forms.
"""

# TODO: timing context managers + counters wired into the pipeline stages.
