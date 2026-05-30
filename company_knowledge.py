COMPANY_NAME = "Campbell's Plumbing & Heating"

COMPANY_INFO = {
    "pricing": (
        "Campbell's uses flat-rate pricing. We do not charge by the hour; "
        "we price by the job and provide the price before doing any work. "
        "All calls have a standard service fee of simply $119."
    ),
    "vip_membership": (
        "The VIP Membership is $350 per year and includes two maintenance visits "
        "on equipment of the customer's choice, plus other perks. It is usually "
        "well worth the price for customers who want maintenance."
    ),
    "service_area": (
        "Campbell's services Bozeman, Belgrade, Gallatin Gateway, Manhattan, "
        "Three Forks, Livingston, and Big Sky."
    ),
    "warranty": (
        "Campbell's guarantees work with a personal workmanship warranty covering "
        "labor and products for one year from completion."
    ),
    "background": (
        "Campbell's Plumbing & Heating is located in Belgrade, Montana. Founded in "
        "1998, the company has decades of experience serving plumbing and heating "
        "needs across the Gallatin Valley with reliable workmanship and customer care."
    ),
}


def get_company_info(topic: str) -> str:
    normalized = (topic or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "price": "pricing",
        "cost": "pricing",
        "fee": "pricing",
        "service_fee": "pricing",
        "trip_charge": "pricing",
        "maintenance": "vip_membership",
        "vip": "vip_membership",
        "membership": "vip_membership",
        "areas": "service_area",
        "service_areas": "service_area",
        "location": "service_area",
        "coverage": "service_area",
        "guarantee": "warranty",
        "workmanship": "warranty",
        "about": "background",
        "company": "background",
    }
    key = aliases.get(normalized, normalized)
    return COMPANY_INFO.get(
        key,
        "I only have details on pricing, VIP Membership, service area, warranty, and company background.",
    )
