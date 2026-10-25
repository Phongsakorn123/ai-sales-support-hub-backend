"""Seed 25 simulated consumer IT products: 5 each of keyboard, mouse, laptop, monitor, headphones.

Usage (from the project root):
    python -m scripts.seed_consumer_catalog

Inserts only SKUs that do not exist yet and never deletes anything. Each product in a category has a
different brand. Prices, costs, stock and warranties are SIMULATED demo data. Images are attached
afterwards with ``python -m scripts.populate_product_images --apply``.
"""

import sys

from sqlalchemy import func, select

from app.backend.models.product import Product
from app.backend.services.products import COLLECTION_FIELDS, _set_collections
from app.db.session import SessionLocal
from scripts.seed_products import _p

PER, COM = "Peripherals", "Computer"
SME_HOME = ["Office Staff", "Home Users"]

PRODUCTS = [
    # Keyboards
    _p(PER, "KBD", 1, "Keychron K8 Wireless Mechanical Keyboard", "Keychron", "K8", "Keyboard", "Mechanical Keyboard",
       "Tenkeyless wireless mechanical keyboard with hot-swappable switches and Mac/Windows layouts.",
       "3290.00", "2400.00", 40, [("Layout", "87-key TKL"), ("Connectivity", "Bluetooth 5.1 / USB-C"), ("Switches", "Gateron Mechanical"), ("Battery", "4000 mAh")],
       ["Hot-swappable switches", "Mac and Windows support", "Multi-device pairing"], 12, ["keyboard", "mechanical", "wireless"],
       ["Developers", "Office Staff"], ["Programming", "Typing"], "Tactile typing with wireless flexibility.", "No numeric keypad."),
    _p(PER, "KBD", 2, "Razer BlackWidow Ultimate Mechanical Gaming Keyboard", "Razer", "BlackWidow Ultimate", "Keyboard", "Gaming Keyboard",
       "Full-size mechanical gaming keyboard with backlit keys and programmable macros.",
       "4190.00", "3100.00", 18, [("Layout", "104-key full size"), ("Connectivity", "USB wired"), ("Switches", "Razer Green"), ("Backlight", "Per-key")],
       ["Programmable macro keys", "Anti-ghosting", "Backlit keys"], 24, ["keyboard", "gaming", "mechanical"],
       ["Gamers", "Streamers"], ["Gaming"], "Responsive keys built for fast input.", "Loud clicky switches."),
    _p(PER, "KBD", 3, "Das Keyboard 4 Professional", "Das Keyboard", "4 Professional", "Keyboard", "Mechanical Keyboard",
       "Premium full-size mechanical keyboard with USB hub and media controls for professional typists.",
       "5290.00", "4000.00", 12, [("Layout", "104-key full size"), ("Connectivity", "USB wired"), ("Switches", "Cherry MX"), ("USB Hub", "2-port USB 3.0")],
       ["Built-in USB hub", "Dedicated volume knob", "Durable build"], 24, ["keyboard", "mechanical", "professional"],
       ["Developers", "Writers"], ["Programming", "Writing"], "Dependable keyboard for long typing sessions.", "No wireless mode."),
    _p(PER, "KBD", 4, "Apple Magic Keyboard", "Apple", "Magic Keyboard", "Keyboard", "Wireless Keyboard",
       "Slim rechargeable wireless keyboard designed for Mac, iPad and other Bluetooth devices.",
       "3500.00", "2800.00", 30, [("Layout", "Full size with numeric keypad variants"), ("Connectivity", "Bluetooth / Lightning"), ("Battery", "Rechargeable"), ("Key Type", "Scissor mechanism")],
       ["Rechargeable battery", "Low-profile keys", "Seamless Apple pairing"], 12, ["keyboard", "wireless", "apple"],
       SME_HOME, ["Office Work", "Mac Setup"], "Clean, quiet typing for Apple users.", "Limited function keys on non-Apple systems."),
    _p(PER, "KBD", 5, "Microsoft Sculpt Ergonomic Keyboard", "Microsoft", "Sculpt Ergonomic", "Keyboard", "Ergonomic Keyboard",
       "Split ergonomic wireless keyboard with cushioned palm rest to reduce wrist strain.",
       "2490.00", "1850.00", 25, [("Layout", "Split ergonomic"), ("Connectivity", "2.4 GHz wireless"), ("Palm Rest", "Cushioned"), ("Battery", "2 x AAA")],
       ["Split key design", "Cushioned palm rest", "Separate numeric pad"], 12, ["keyboard", "ergonomic", "wireless"],
       ["Office Staff", "Writers"], ["Long Typing Sessions"], "Comfortable posture for all-day typing.", "Takes time to adapt to the layout."),
    # Mice
    _p(PER, "MSE", 1, "Razer DeathAdder Elite Gaming Mouse", "Razer", "DeathAdder Elite", "Mouse", "Gaming Mouse",
       "Ergonomic wired gaming mouse with a high-precision optical sensor.",
       "1990.00", "1400.00", 35, [("Sensor", "Optical 16,000 DPI"), ("Buttons", "7 programmable"), ("Connectivity", "USB wired"), ("Lighting", "RGB")],
       ["Ergonomic right-handed shape", "Programmable buttons", "RGB lighting"], 24, ["mouse", "gaming", "wired"],
       ["Gamers"], ["Gaming"], "Accurate tracking with comfortable grip.", "Right-handed only."),
    _p(PER, "MSE", 2, "Logitech G502 Proteus Gaming Mouse", "Logitech", "G502", "Mouse", "Gaming Mouse",
       "Wired gaming mouse with adjustable weights and customizable DPI profiles.",
       "1790.00", "1250.00", 28, [("Sensor", "Optical 12,000 DPI"), ("Buttons", "11 programmable"), ("Connectivity", "USB wired"), ("Weights", "Tunable")],
       ["Adjustable weight system", "DPI shift button", "Hyper-fast scroll wheel"], 24, ["mouse", "gaming", "wired"],
       ["Gamers", "Power Users"], ["Gaming", "Productivity"], "Highly customizable for different workflows.", "Heavier than office mice."),
    _p(PER, "MSE", 3, "Apple Magic Mouse", "Apple", "Magic Mouse", "Mouse", "Wireless Mouse",
       "Rechargeable wireless mouse with a Multi-Touch surface.",
       "3100.00", "2500.00", 22, [("Connectivity", "Bluetooth / Lightning"), ("Battery", "Rechargeable"), ("Surface", "Multi-Touch"), ("Buttons", "None (touch surface)")],
       ["Multi-Touch gestures", "Rechargeable", "Slim design"], 12, ["mouse", "wireless", "apple"],
       SME_HOME, ["Mac Setup", "Office Work"], "Gesture control that suits macOS.", "Not comfortable for long gaming sessions."),
    _p(PER, "MSE", 4, "Microsoft Pro IntelliMouse", "Microsoft", "Pro IntelliMouse", "Mouse", "Office Mouse",
       "Wired optical mouse with a symmetrical shape and a precise sensor for everyday work.",
       "1290.00", "900.00", 45, [("Sensor", "Optical 16,000 DPI"), ("Connectivity", "USB wired"), ("Buttons", "5"), ("Lighting", "Backlit logo")],
       ["Precise tracking", "Classic shape", "Durable switches"], 12, ["mouse", "wired", "office"],
       ["Office Staff", "Students"], ["Office Work"], "Dependable everyday mouse.", "Wired only."),
    _p(PER, "MSE", 5, "SteelSeries Rival Gaming Mouse", "SteelSeries", "Rival", "Mouse", "Gaming Mouse",
       "Esports gaming mouse with a customizable optical sensor and RGB lighting.",
       "1590.00", "1100.00", 20, [("Sensor", "Optical 6,500 DPI"), ("Buttons", "6 programmable"), ("Connectivity", "USB wired"), ("Lighting", "RGB")],
       ["Esports-grade sensor", "Programmable buttons", "RGB lighting"], 24, ["mouse", "gaming", "esports"],
       ["Gamers", "Esports Teams"], ["Gaming"], "Consistent performance for competitive play.", "No wireless option."),
    # Laptops
    _p(COM, "LPT", 1, "Apple MacBook Air 13 M2", "Apple", "MacBook Air M2", "Laptop", "Ultrabook",
       "Thin and light laptop with the Apple M2 chip for everyday work and creative tasks.",
       "42900.00", "37500.00", 14, [("Processor", "Apple M2"), ("Memory", "8 GB"), ("Storage", "256 GB SSD"), ("Display", "13.6-inch Liquid Retina")],
       ["Fanless design", "All-day battery", "MagSafe charging"], 12, ["laptop", "ultrabook", "macos"],
       ["Professionals", "Students"], ["Mobile Work", "Creative Work"], "Lightweight with strong battery life.", "Limited ports and no GPU upgrade."),
    _p(COM, "LPT", 2, "Dell XPS 13 Laptop", "Dell", "XPS 13", "Laptop", "Premium Ultrabook",
       "Compact premium Windows ultrabook with an edge-to-edge display.",
       "45900.00", "39800.00", 10, [("Processor", "Intel Core Ultra 7"), ("Memory", "16 GB"), ("Storage", "512 GB SSD"), ("Display", "13.4-inch")],
       ["Edge-to-edge display", "CNC aluminium chassis", "Fast charging"], 24, ["laptop", "ultrabook", "windows"],
       ["Professionals", "Executives"], ["Mobile Work", "Meetings"], "Premium build in a small package.", "Few ports."),
    _p(COM, "LPT", 3, "ASUS Zenbook 14 Laptop", "ASUS", "Zenbook 14", "Laptop", "Ultrabook",
       "Slim laptop with a vivid display and a long battery life for work and travel.",
       "32900.00", "27800.00", 16, [("Processor", "AMD Ryzen 7"), ("Memory", "16 GB"), ("Storage", "512 GB SSD"), ("Display", "14-inch OLED")],
       ["OLED display", "Lightweight", "Fast charging"], 24, ["laptop", "ultrabook", "oled"],
       ["Professionals", "Students"], ["Mobile Work", "Study"], "Great display for the price.", "Integrated graphics only."),
    _p(COM, "LPT", 4, "Acer Swift 7 Laptop", "Acer", "Swift 7", "Laptop", "Ultrabook",
       "Ultra-thin laptop with a metal body and a fingerprint reader for professionals on the go.",
       "38900.00", "33000.00", 8, [("Processor", "Intel Core i7"), ("Memory", "16 GB"), ("Storage", "1 TB SSD"), ("Display", "14-inch Full HD")],
       ["Ultra-thin metal body", "Fingerprint reader", "Wi-Fi 6"], 24, ["laptop", "ultrabook", "thin"],
       ["Professionals", "Executives"], ["Mobile Work"], "Very portable.", "Shallow keyboard travel."),
    _p(COM, "LPT", 5, "Microsoft Surface Laptop 7", "Microsoft", "Surface Laptop 7", "Laptop", "Premium Laptop",
       "Premium laptop with a touchscreen and an AI-ready processor for Windows 11.",
       "39900.00", "34200.00", 12, [("Processor", "Snapdragon X Plus"), ("Memory", "16 GB"), ("Storage", "512 GB SSD"), ("Display", "13.8-inch touchscreen")],
       ["Touchscreen", "Copilot+ PC", "Long battery life"], 12, ["laptop", "touchscreen", "windows"],
       ["Professionals", "Students"], ["Mobile Work", "Presentations"], "Elegant and responsive daily laptop.", "Some x86 apps run under emulation."),
    # Monitors
    _p(PER, "MNT", 1, "BenQ ZOWIE XL2420T Gaming Monitor", "BenQ", "XL2420T", "Monitor", "Gaming Monitor",
       "24-inch 144 Hz gaming monitor with fast response for esports.",
       "9900.00", "7800.00", 15, [("Size", "24-inch"), ("Resolution", "1920 x 1080"), ("Refresh Rate", "144 Hz"), ("Panel", "TN")],
       ["144 Hz refresh rate", "Height adjustable stand", "Black eQualizer"], 36, ["monitor", "gaming", "144hz"],
       ["Gamers", "Esports Teams"], ["Gaming"], "Smooth motion for competitive play.", "TN panel has narrower viewing angles."),
    _p(PER, "MNT", 2, "ASUS ProArt PA246Q Monitor", "ASUS", "ProArt PA246Q", "Monitor", "Professional Monitor",
       "24-inch colour-accurate monitor for photo and design work.",
       "14900.00", "11800.00", 9, [("Size", "24-inch"), ("Resolution", "1920 x 1200"), ("Panel", "IPS"), ("Colour", "10-bit, 99% Adobe RGB")],
       ["Wide colour gamut", "Hardware calibration", "Height adjustable stand"], 36, ["monitor", "color-accurate", "design"],
       ["Designers", "Photographers"], ["Photo Editing", "Design"], "Reliable colour for creative workflows.", "Not designed for fast gaming."),
    _p(PER, "MNT", 3, "AOC 27B2H Monitor", "AOC", "27B2H", "Monitor", "Office Monitor",
       "27-inch Full HD IPS monitor for office and everyday use.",
       "3990.00", "3000.00", 40, [("Size", "27-inch"), ("Resolution", "1920 x 1080"), ("Panel", "IPS"), ("Ports", "HDMI / VGA")],
       ["Frameless design", "Low blue light mode", "VESA mount"], 36, ["monitor", "office", "ips"],
       ["Office Staff", "Students"], ["Office Work"], "Affordable large display.", "Full HD only."),
    _p(PER, "MNT", 4, "LG UltraFine 5K Display", "LG", "UltraFine 5K", "Monitor", "Professional Monitor",
       "27-inch 5K display with Thunderbolt 3 for Mac users and creative professionals.",
       "42900.00", "36000.00", 5, [("Size", "27-inch"), ("Resolution", "5120 x 2880"), ("Panel", "IPS"), ("Connectivity", "Thunderbolt 3")],
       ["5K resolution", "Thunderbolt 3 charging", "Built-in camera and speakers"], 24, ["monitor", "5k", "thunderbolt"],
       ["Designers", "Video Editors"], ["Video Editing", "Design"], "Very sharp image and one-cable setup.", "Optimized for Mac."),
    _p(PER, "MNT", 5, "Dell UltraSharp U3415W Monitor", "Dell", "U3415W", "Monitor", "Ultrawide Monitor",
       "34-inch curved ultrawide monitor for multitasking and spreadsheets.",
       "25900.00", "21000.00", 7, [("Size", "34-inch curved"), ("Resolution", "3440 x 1440"), ("Panel", "IPS"), ("Ports", "HDMI / DisplayPort / USB hub")],
       ["Ultrawide 21:9", "Picture-by-picture", "USB hub"], 36, ["monitor", "ultrawide", "productivity"],
       ["Analysts", "Developers"], ["Multitasking", "Data Analysis"], "More screen space in one display.", "Large desk footprint."),
    # Headphones
    _p(PER, "HDP", 1, "Bose QuietComfort 35 II Wireless Headphones", "Bose", "QuietComfort 35 II", "Headphones", "Noise Cancelling Headphones",
       "Wireless over-ear headphones with active noise cancelling and voice assistant support.",
       "11900.00", "9300.00", 20, [("Type", "Over-ear wireless"), ("Noise Cancelling", "Active"), ("Battery", "Up to 20 hours"), ("Connectivity", "Bluetooth / NFC")],
       ["Active noise cancelling", "Comfortable fit", "Voice assistant button"], 12, ["headphones", "noise-cancelling", "wireless"],
       ["Travellers", "Office Staff"], ["Travel", "Focus Work"], "Quiet and comfortable for long use.", "Older Bluetooth version."),
    _p(PER, "HDP", 2, "Sennheiser HD 598 Headphones", "Sennheiser", "HD 598", "Headphones", "Hi-Fi Headphones",
       "Open-back wired headphones with a warm, spacious sound for home listening.",
       "5290.00", "3900.00", 15, [("Type", "Over-ear open-back"), ("Impedance", "50 ohm"), ("Connectivity", "3.5 mm wired"), ("Cable", "Detachable")],
       ["Open-back design", "Velour ear pads", "Detachable cable"], 24, ["headphones", "hi-fi", "wired"],
       ["Audiophiles", "Home Users"], ["Music Listening"], "Natural and spacious sound.", "Leaks sound, not for noisy places."),
    _p(PER, "HDP", 3, "Sony MDR-7506 Studio Headphones", "Sony", "MDR-7506", "Headphones", "Studio Headphones",
       "Closed-back studio monitor headphones used for recording and editing.",
       "3190.00", "2300.00", 30, [("Type", "Over-ear closed-back"), ("Impedance", "63 ohm"), ("Connectivity", "3.5 mm wired / 6.3 mm adapter"), ("Folding", "Yes")],
       ["Studio monitoring sound", "Foldable design", "Coiled cable"], 12, ["headphones", "studio", "wired"],
       ["Audio Engineers", "Content Creators"], ["Recording", "Editing"], "Clear, accurate sound for monitoring.", "Wired only."),
    _p(PER, "HDP", 4, "Audio-Technica ATH-M50 Headphones", "Audio-Technica", "ATH-M50", "Headphones", "Studio Headphones",
       "Professional closed-back headphones with strong bass and detailed response.",
       "4290.00", "3200.00", 26, [("Type", "Over-ear closed-back"), ("Impedance", "38 ohm"), ("Connectivity", "3.5 mm wired"), ("Driver", "45 mm")],
       ["Detailed sound", "Swivel ear cups", "Detachable cable"], 12, ["headphones", "studio", "wired"],
       ["Content Creators", "Musicians"], ["Mixing", "Music Listening"], "Popular all-round monitoring headphones.", "Wired only."),
    _p(PER, "HDP", 5, "JBL Live 650BTNC Headphones", "JBL", "Live 650BTNC", "Headphones", "Noise Cancelling Headphones",
       "Wireless over-ear headphones with noise cancelling and voice assistant support.",
       "4990.00", "3700.00", 24, [("Type", "Over-ear wireless"), ("Noise Cancelling", "Active"), ("Battery", "Up to 30 hours"), ("Connectivity", "Bluetooth 5.0")],
       ["Active noise cancelling", "Ambient Aware mode", "Fast charging"], 12, ["headphones", "noise-cancelling", "wireless"],
       ["Commuters", "Home Users"], ["Commuting", "Music Listening"], "Good battery life at a moderate price.", "Noise cancelling is milder than premium models."),
]


def validate() -> None:
    assert len(PRODUCTS) == 25 and len({p["sku"] for p in PRODUCTS}) == 25
    by_type: dict[str, list[str]] = {}
    for p in PRODUCTS:
        by_type.setdefault(p["type"], []).append(p["brand"])
    assert set(by_type) == {"Keyboard", "Mouse", "Laptop", "Monitor", "Headphones"}
    for type_, brands in by_type.items():
        assert len(brands) == 5 and len(set(brands)) == 5, f"{type_}: brands must be 5 distinct values"


def main() -> int:
    validate()
    with SessionLocal() as session:
        try:
            existing = set(session.scalars(select(Product.sku).where(Product.sku.in_([p["sku"] for p in PRODUCTS]))))
            inserted = 0
            for data in PRODUCTS:
                if data["sku"] in existing:
                    continue
                values = dict(data)
                collections = {field: values.pop(field) for field in COLLECTION_FIELDS}
                product = Product(**values)
                _set_collections(product, collections)
                session.add(product)
                inserted += 1
            session.commit()
        except Exception as error:
            session.rollback()
            print(f"Seeding failed, rolled back: {error}", file=sys.stderr)
            return 1
        total = session.scalar(select(func.count()).select_from(Product))
    print(f"Inserted {inserted}, skipped {len(existing)}. Total products: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
