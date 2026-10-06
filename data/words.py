"""Word lists for the made-up warehouse. Every product, campaign and person is fictional."""

from datetime import date

# The shop opened on 2024-01-01. Regions launched one after another.
# (id, name, countries with weights, launch date, share of new sign-ups once launched)
REGIONS = [
    (1, "Nordics", [("SE", 4), ("DK", 3), ("NO", 2), ("FI", 2)], date(2024, 1, 1), 12),
    (2, "DACH", [("DE", 8), ("AT", 2), ("CH", 2)], date(2024, 1, 1), 24),
    (3, "Benelux", [("NL", 5), ("BE", 4), ("LU", 1)], date(2024, 1, 1), 12),
    (4, "France", [("FR", 1)], date(2024, 1, 1), 18),
    (5, "Iberia", [("ES", 3), ("PT", 1)], date(2024, 6, 1), 10),
    (6, "Italy", [("IT", 1)], date(2024, 9, 1), 10),
    (7, "UK and Ireland", [("GB", 4), ("IE", 1)], date(2025, 3, 1), 10),
    (8, "Central Europe", [("PL", 4), ("CZ", 2), ("HU", 1), ("SK", 1)], date(2025, 9, 1), 6),
]

# (id, name, department, price range as indexes into PRICE_POINTS, refund propensity per 1,000 orders)
CATEGORIES = [
    (1, "Cookware", "Kitchen", (8, 20), 35),
    (2, "Tableware", "Kitchen", (2, 16), 45),
    (3, "Kitchen Tools", "Kitchen", (0, 11), 25),
    (4, "Coffee and Tea", "Kitchen", (3, 19), 35),
    (5, "Bedding", "Home", (7, 21), 40),
    (6, "Bath", "Home", (2, 15), 30),
    (7, "Lighting", "Home", (8, 23), 80),
    (8, "Decor", "Home", (3, 18), 40),
    (9, "Garden Tools", "Garden", (2, 16), 30),
    (10, "Planters", "Garden", (3, 18), 45),
    (11, "Outdoor Furniture", "Garden", (15, 29), 120),
    (12, "Outdoor Cooking", "Garden", (9, 27), 70),
]

PRICE_POINTS = [
    "4.90", "7.90", "9.90", "12.90", "14.90", "19.90", "24.90", "29.90", "34.90", "39.90",
    "44.90", "49.90", "59.00", "69.00", "79.00", "89.00", "99.00", "119.00", "129.00", "149.00",
    "179.00", "199.00", "249.00", "299.00", "349.00", "399.00", "499.00", "599.00", "749.00", "899.00",
]  # fmt: skip

# Ten items per category, each in three variants; twenty of the thirty become products.
PRODUCT_ITEMS = {
    "Cookware": (
        ["Frying Pan", "Saucepan", "Stockpot", "Wok", "Casserole", "Grill Pan", "Saute Pan", "Roasting Tin",
         "Milk Pan", "Paella Pan"],
        ["Cast Iron", "Stainless Steel", "Non-Stick"],
    ),
    "Tableware": (
        ["Dinner Plates", "Side Plates", "Pasta Bowls", "Mugs", "Serving Platter", "Salad Bowl", "Cereal Bowls",
         "Espresso Cups", "Butter Dish", "Gravy Boat"],
        ["Stoneware", "Porcelain", "Terracotta"],
    ),
    "Kitchen Tools": (
        ["Spatula", "Chopping Board", "Rolling Pin", "Salad Servers", "Spoon Set", "Serving Board",
         "Utensil Holder", "Pepper Mill", "Salt Box", "Knife Block"],
        ["Beech", "Olive Wood", "Acacia"],
    ),
    "Coffee and Tea": (
        ["French Press", "Moka Pot", "Teapot", "Pour-Over Dripper", "Milk Jug", "Kettle", "Tea Caddy",
         "Coffee Canister", "Tea Strainer", "Coffee Scoop"],
        ["Steel", "Copper", "Matte Black"],
    ),
    "Bedding": (
        ["Duvet Cover", "Fitted Sheet", "Flat Sheet", "Pillowcase Pair", "Throw", "Bedspread", "Blanket",
         "Cushion", "Bed Runner", "Quilt"],
        ["Linen", "Organic Cotton", "Percale"],
    ),
    "Bath": (
        ["Bath Towel", "Hand Towel", "Bath Sheet", "Bath Mat", "Bathrobe", "Guest Towel Set", "Washcloth Set",
         "Beach Towel", "Hooded Towel", "Towel Bale"],
        ["Waffle", "Turkish Cotton", "Bamboo"],
    ),
    "Lighting": (
        ["Table Lamp", "Floor Lamp", "Pendant Light", "Wall Light", "Desk Lamp", "Bedside Lamp", "Ceiling Light",
         "Reading Lamp", "Lantern", "Arc Lamp"],
        ["Brass", "Rattan", "Oak"],
    ),
    "Decor": (
        ["Vase", "Mirror", "Photo Frame", "Candle Holder", "Wall Clock", "Tray", "Bowl", "Bookends",
         "Coaster Set", "Trinket Box"],
        ["Walnut", "Brass", "Marble"],
    ),
    "Garden Tools": (
        ["Spade", "Garden Fork", "Rake", "Hand Trowel", "Hoe", "Pruning Shears", "Hedge Shears", "Loppers",
         "Weeder", "Bulb Planter"],
        ["Classic", "Pro", "Compact"],
    ),
    "Planters": (
        ["Planter", "Window Box", "Hanging Basket", "Raised Bed", "Pot Set", "Trough", "Herb Planter",
         "Rail Planter", "Saucer Set", "Seed Tray"],
        ["Terracotta", "Zinc", "Wooden"],
    ),
    "Outdoor Furniture": (
        ["Garden Bench", "Bistro Set", "Lounge Chair", "Dining Table", "Sun Lounger", "Parasol", "Hammock",
         "Folding Chair", "Garden Sofa", "Side Table"],
        ["Teak", "Acacia", "Aluminium"],
    ),
    "Outdoor Cooking": (
        ["Charcoal Barbecue", "Gas Barbecue", "Pizza Oven", "Smoker", "Fire Pit", "Plancha", "Grill Tool Set",
         "Barbecue Cover", "Skewer Set", "Chimney Starter"],
        ["Compact", "Classic", "Pro"],
    ),
}  # fmt: skip

# Relative demand per category and month (1 = January). Garden sells in spring, home goods in winter.
SEASON = {
    "Garden Tools": [0.4, 0.5, 1.4, 2.2, 2.2, 1.6, 1.2, 1.0, 0.9, 0.7, 0.4, 0.4],
    "Planters": [0.4, 0.5, 1.5, 2.3, 2.2, 1.5, 1.1, 0.9, 0.8, 0.6, 0.4, 0.4],
    "Outdoor Furniture": [0.3, 0.4, 1.0, 1.8, 2.4, 2.3, 1.8, 1.2, 0.6, 0.4, 0.3, 0.3],
    "Outdoor Cooking": [0.3, 0.3, 0.6, 1.2, 2.3, 2.6, 2.4, 1.6, 0.8, 0.5, 0.6, 0.8],
    "Bedding": [1.3, 1.0, 0.9, 0.8, 0.7, 0.7, 0.7, 0.8, 1.0, 1.3, 1.5, 1.4],
    "Lighting": [1.1, 0.9, 0.8, 0.7, 0.6, 0.6, 0.6, 0.7, 1.0, 1.4, 1.6, 1.5],
    "Decor": [0.9, 0.9, 0.9, 0.9, 0.8, 0.8, 0.8, 0.8, 1.0, 1.1, 1.6, 1.8],
    "Coffee and Tea": [1.0, 1.0, 0.9, 0.9, 0.8, 0.8, 0.8, 0.8, 1.0, 1.1, 1.4, 1.8],
}

# How likely an order placed in each month is to happen at all (thins the order stream).
ORDER_SEASON = [0.80, 0.75, 0.85, 0.90, 0.92, 0.88, 0.82, 0.82, 0.92, 0.96, 1.00, 1.00]

# Black Friday weeks: extra orders, Monday to Monday.
BLACK_FRIDAY_WEEKS = [(date(2024, 11, 25), date(2024, 12, 2)), (date(2025, 11, 24), date(2025, 12, 1))]

ACQUISITION_CHANNELS = [
    ("organic search", 30),
    ("paid search", 22),
    ("social", 18),
    ("email", 8),
    ("referral", 10),
    ("marketplace", 9),
    (None, 3),
]

REFUND_REASONS = ["damaged in transit", "wrong item", "late delivery", "not as described", "changed mind"]

# (name, channel, start, end, budget in EUR, discount %, department or None for the whole shop)
CAMPAIGNS = [
    ("Fresh Start 2024", "email", date(2024, 1, 8), date(2024, 1, 28), "12000.00", 10, None),
    ("Table for Two 2024", "social", date(2024, 2, 5), date(2024, 2, 14), "8000.00", 15, "Kitchen"),
    ("Spring Garden 2024", "search", date(2024, 3, 18), date(2024, 4, 21), "30000.00", 15, "Garden"),
    ("Balcony Days 2024", "social", date(2024, 5, 6), date(2024, 5, 26), "14000.00", 10, "Garden"),
    ("Summer Grill 2024", "display", date(2024, 6, 3), date(2024, 6, 30), "22000.00", 15, "Garden"),
    ("Back Home 2024", "email", date(2024, 9, 2), date(2024, 9, 22), "10000.00", 10, "Home"),
    ("Cosy Nights 2024", "social", date(2024, 10, 14), date(2024, 11, 3), "18000.00", 15, "Home"),
    ("Black Friday 2024", "search", date(2024, 11, 25), date(2024, 12, 2), "60000.00", 20, None),
    ("Gift Guide 2024", "affiliate", date(2024, 12, 3), date(2024, 12, 20), "26000.00", 0, None),
    ("Fresh Start 2025", "email", date(2025, 1, 6), date(2025, 1, 26), "14000.00", 10, None),
    ("Table for Two 2025", "social", date(2025, 2, 3), date(2025, 2, 14), "9000.00", 15, "Kitchen"),
    ("Spring Garden 2025", "search", date(2025, 3, 17), date(2025, 4, 20), "38000.00", 15, "Garden"),
    ("UK Launch 2025", "display", date(2025, 3, 3), date(2025, 3, 30), "45000.00", 10, None),
    ("Balcony Days 2025", "social", date(2025, 5, 5), date(2025, 5, 25), "16000.00", 10, "Garden"),
    ("Summer Grill 2025", "display", date(2025, 6, 2), date(2025, 6, 29), "26000.00", 15, "Garden"),
    ("Back Home 2025", "email", date(2025, 9, 1), date(2025, 9, 21), "12000.00", 10, "Home"),
    ("Hello Central Europe 2025", "display", date(2025, 9, 1), date(2025, 9, 30), "30000.00", 10, None),
    ("Cosy Nights 2025", "social", date(2025, 10, 13), date(2025, 11, 2), "21000.00", 15, "Home"),
    ("Black Friday 2025", "search", date(2025, 11, 24), date(2025, 12, 1), "75000.00", 20, None),
    ("Gift Guide 2025", "affiliate", date(2025, 12, 2), date(2025, 12, 19), "30000.00", 0, None),
    ("Fresh Start 2026", "email", date(2026, 1, 5), date(2026, 1, 25), "15000.00", 10, None),
    ("Table for Two 2026", "social", date(2026, 2, 2), date(2026, 2, 14), "10000.00", 15, "Kitchen"),
    ("Spring Garden 2026", "search", date(2026, 3, 16), date(2026, 4, 19), "42000.00", 15, "Garden"),
    ("Balcony Days 2026", "social", date(2026, 5, 4), date(2026, 5, 24), "18000.00", 10, "Garden"),
    ("Summer Grill 2026", "display", date(2026, 6, 1), date(2026, 6, 28), "30000.00", 15, "Garden"),
    ("Coffee Week 2026", "affiliate", date(2026, 7, 6), date(2026, 7, 12), "6000.00", 20, "Kitchen"),
    ("Back Home 2026", "email", date(2026, 8, 31), date(2026, 9, 20), "14000.00", 10, "Home"),
]

FIRST_NAMES = """Aino Alba Alessio Amelie Anders Anna Antoine Astrid Aurelie Beatriz Bram Camille Carla Chiara
Clara Daan Diego Elena Elias Emil Emma Enzo Eva Fabian Femke Finn Freya Giulia Hanna Hugo Ines Isak Jakub
Jan Jonas Julia Kasper Klara Lara Lars Laura Lea Leon Liam Lina Lotte Louis Luca Lucia Maja Marco Margot
Marie Mateo Mathis Matteo Maya Mila Milan Nico Nina Noah Nora Olivia Oscar Paula Pierre Rafael Rosa Ruben
Sara Siena Sofia Sven Tessa Theo Tomas Vera Victor Wiktor Zoe""".split()

LAST_NAMES = """Aalto Andersen Bakker Barros Bauer Bergman Bianchi Blom Carvalho Costa Dahl Dekker Dubois
Eriksen Esposito Fischer Fontaine Garcia Gomez Hansen Hartmann Holm Jansen Kowalski Kraus Lambert Larsen
Laurent Lehmann Lindqvist Lopez Marino Martin Meyer Moreau Navarro Nielsen Novak Peeters Petit Pereira
Rossi Ruiz Santos Schmidt Silva Smit Sorensen Thomas Vasquez Visser Wagner Weber Wouters Zielinski""".split()

STREETS = """Birch Lane|Mill Road|Orchard Way|Harbour Street|Linden Avenue|Chestnut Row|Meadow Close|Station Road
|Willow Walk|Market Square|Cedar Court|River Lane|Garden Street|Oak Terrace|Elm Grove|Maple Drive""".replace(
    "\n", ""
).split("|")

CITIES = {
    "SE": ["Stockholm", "Gothenburg", "Malmo", "Uppsala"],
    "DK": ["Copenhagen", "Aarhus", "Odense"],
    "NO": ["Oslo", "Bergen", "Trondheim"],
    "FI": ["Helsinki", "Espoo", "Tampere"],
    "DE": ["Berlin", "Hamburg", "Munich", "Cologne", "Leipzig", "Stuttgart"],
    "AT": ["Vienna", "Graz", "Linz"],
    "CH": ["Zurich", "Basel", "Bern"],
    "NL": ["Amsterdam", "Rotterdam", "Utrecht", "Eindhoven"],
    "BE": ["Brussels", "Antwerp", "Ghent"],
    "LU": ["Luxembourg"],
    "FR": ["Paris", "Lyon", "Marseille", "Toulouse", "Nantes", "Lille"],
    "ES": ["Madrid", "Barcelona", "Valencia", "Seville"],
    "PT": ["Lisbon", "Porto"],
    "IT": ["Milan", "Rome", "Turin", "Bologna", "Florence"],
    "GB": ["London", "Manchester", "Bristol", "Leeds", "Edinburgh"],
    "IE": ["Dublin", "Cork"],
    "PL": ["Warsaw", "Krakow", "Wroclaw", "Gdansk"],
    "CZ": ["Prague", "Brno"],
    "HU": ["Budapest"],
    "SK": ["Bratislava"],
}

EMAIL_DOMAINS = ["example.com", "example.org", "example.net"]
