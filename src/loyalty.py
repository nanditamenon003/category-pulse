"""
Loyalty tier playbooks.

This is a Phase 4 stub — full implementation lands in Phase 6f, where it
reads data/loyalty_tiers.csv and ranks each tier's response profile for a
category so the agent can pair a cross-sell recommendation with the tier
most likely to respond to it. Segment-level only, by design: see the note
in Phase 6f about why individual-customer targeting is deliberately out of
scope for this project.
"""


def get_tier_playbook(category):
    """
    Each loyalty tier's response profile for a category, ranked by
    cross-sell response rate. Not yet available — see Phase 6f.
    """
    return {
        "category": category,
        "note": "Tier playbook data is not yet available (Phase 6f).",
    }
