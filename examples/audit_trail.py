"""What did your agent know on the day it answered?

A support agent told customer 4821 they had priority support. That was in
March. In June the customer moved to a cheaper plan. Today they are angry,
quoting your bot back at you.

Was the bot wrong, or was it right at the time?

Most memory stores cannot answer that. They keep the current state, so the
March answer looks like a mistake even when it was correct. Korely keeps both:
every fact carries the window it was true in, so you can ask what the store
believed on any past date.

Run it:

    pip install korely-memory
    korely init --agent --agent-caller audit-example
    python audit_trail.py

Takes about 20 seconds, most of it waiting for the facts to be extracted.
"""

import time

from korely_memory import Korely

korely = Korely()  # reads the key korely init saved, or KORELY_API_KEY
CUSTOMER = "customer-4821"


def show(label, **query):
    facts = korely.get_facts(user_id=CUSTOMER, **query)
    print(f"\n  {label}")
    if not facts:
        print("      (nothing on record yet)")
    for f in facts:
        print(f"      {f.subject} · {f.predicate} · {f.object}")


# Write history the way it happened, not the way it arrived. `timestamp` is
# what makes this work: facts inherit it as valid_from, so `as_of` answers
# over the world's timeline instead of your ingestion order.
korely.add(
    "Customer 4821 is on the Business plan, which includes priority support.",
    user_id=CUSTOMER,
    timestamp="2026-03-15",
)
korely.add(
    "Customer 4821 moved to the Standard plan. Priority support no longer applies.",
    user_id=CUSTOMER,
    timestamp="2026-06-01",
)

print("Extracting facts, this takes a few seconds...")
time.sleep(15)

print("\nThe complaint: \"your bot promised me priority support in March.\"")

show("What the store believed in March, when the agent answered:", as_of="2026-03-20")
show("What is true today:")

print(
    "\n  The agent was right in March and right today. Both are provable,\n"
    "  because the old fact was superseded rather than overwritten.\n"
)

# The superseded fact is still there, carrying the moment it stopped being true.
print("  The full record, superseded entries included:")
for f in korely.get_facts(user_id=CUSTOMER, include_invalidated=True):
    until = f.invalid_at[:10] if f.invalid_at else "still true"
    print(f"      {f.subject} · {f.predicate} · {f.object}   (until: {until})")

# Clean up after yourself. This is the GDPR Article 17 endpoint, and the point
# of an example is to prove things rather than assert them, so we check instead
# of trusting: read it back the most permissive way we can and count what is
# left. Anything above zero would mean the data was hidden, not erased.
korely.delete_all(user_id=CUSTOMER)
left = korely.get_facts(user_id=CUSTOMER, include_invalidated=True)
print(f"\n  Erased the example customer. Facts still readable: {len(left)}")
print("  Zero is the answer you want, and the one you should check for yourself\n"
      "  on any memory service that claims to delete.\n")
