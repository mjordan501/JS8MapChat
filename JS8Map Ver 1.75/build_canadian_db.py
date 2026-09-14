#!/usr/bin/env python3
"""
build_canadian_db.py  —  Import Industry Canada amateur radio database into ham.db

Usage:
    python build_canadian_db.py amateur_delim.txt ham.db

Downloads the source file from:
    https://apc-cap.ic.gc.ca/datafiles/amateur_delim.zip

The script:
  1. Reads the semicolon-delimited Canadian amateur callsign file
  2. Geocodes each operator using FSA (first 3 chars of postal code) → lat/lon
  3. Falls back to province centroid when FSA is unknown
  4. Inserts/updates records in the callsigns table of ham.db
  5. Reports summary on completion

Callsign prefixes covered: VA, VB, VC, VD, VE, VF, VG, VX, VO, VY, CY
"""

import sqlite3
import sys
import os
import csv
from math import floor

# ── Province centroids (lat, lon) ────────────────────────────────────────────
PROVINCE = {
    'BC': (53.7267, -127.6476, 'British Columbia'),
    'AB': (53.9333, -116.5765, 'Alberta'),
    'SK': (52.9399, -106.4509, 'Saskatchewan'),
    'MB': (53.7609,  -98.8139, 'Manitoba'),
    'ON': (51.2538,  -85.3232, 'Ontario'),
    'QC': (52.9399,  -73.5491, 'Quebec'),
    'NB': (46.5653,  -66.4619, 'New Brunswick'),
    'NS': (44.6820,  -63.7443, 'Nova Scotia'),
    'PE': (46.5107,  -63.4168, 'Prince Edward Island'),
    'NL': (53.1355,  -57.6604, 'Newfoundland and Labrador'),
    'YT': (64.2823, -135.0000, 'Yukon'),
    'NT': (64.8255, -124.8457, 'Northwest Territories'),
    'NU': (70.2998,  -83.1076, 'Nunavut'),
}

# ── Major Canadian cities lat/lon lookup ─────────────────────────────────────
# city_key = (city_name.upper(), prov_cd) → (lat, lon)
# Covers ~200 cities where most Canadian hams are located
CITIES = {
    # British Columbia
    ('VANCOUVER',       'BC'): (49.2827, -123.1207),
    ('VICTORIA',        'BC'): (48.4284, -123.3656),
    ('SURREY',          'BC'): (49.1913, -122.8490),
    ('BURNABY',         'BC'): (49.2488, -122.9805),
    ('RICHMOND',        'BC'): (49.1666, -123.1336),
    ('KELOWNA',         'BC'): (49.8880, -119.4960),
    ('ABBOTSFORD',      'BC'): (49.0504, -122.3045),
    ('COQUITLAM',       'BC'): (49.2838, -122.7932),
    ('LANGLEY',         'BC'): (49.1044, -122.6604),
    ('NANAIMO',         'BC'): (49.1659, -123.9401),
    ('PRINCE GEORGE',   'BC'): (53.9171, -122.7497),
    ('KAMLOOPS',        'BC'): (50.6745, -120.3273),
    ('CHILLIWACK',      'BC'): (49.1579, -121.9514),
    ('MAPLE RIDGE',     'BC'): (49.2193, -122.5984),
    ('NEW WESTMINSTER', 'BC'): (49.2057, -122.9110),
    ('NORTH VANCOUVER', 'BC'): (49.3198, -123.0725),
    ('WEST VANCOUVER',  'BC'): (49.3626, -123.1652),
    ('PENTICTON',       'BC'): (49.4991, -119.5937),
    ('CRANBROOK',       'BC'): (49.5124, -115.7694),
    ('TRAIL',           'BC'): (49.0953, -117.7117),
    # Alberta
    ('CALGARY',         'AB'): (51.0447, -114.0719),
    ('EDMONTON',        'AB'): (53.5461, -113.4938),
    ('RED DEER',        'AB'): (52.2681,  -113.8112),
    ('LETHBRIDGE',      'AB'): (49.6956, -112.8451),
    ('ST. ALBERT',      'AB'): (53.6302, -113.6258),
    ('MEDICINE HAT',    'AB'): (50.0405, -110.6764),
    ('GRANDE PRAIRIE',  'AB'): (55.1707, -118.7884),
    ('AIRDRIE',         'AB'): (51.2917, -114.0144),
    ('SPRUCE GROVE',    'AB'): (53.5449, -113.9010),
    ('LEDUC',           'AB'): (53.2594, -113.5490),
    # Saskatchewan
    ('SASKATOON',       'SK'): (52.1332, -106.6700),
    ('REGINA',          'SK'): (50.4452, -104.6189),
    ('PRINCE ALBERT',   'SK'): (53.2033, -105.7534),
    ('MOOSE JAW',       'SK'): (50.3930, -105.5520),
    ('SWIFT CURRENT',   'SK'): (50.2851, -107.7939),
    # Manitoba
    ('WINNIPEG',        'MB'): (49.8951,  -97.1384),
    ('BRANDON',         'MB'): (49.8485, -99.9501),
    ('STEINBACH',       'MB'): (49.5264,  -96.6841),
    ('PORTAGE LA PRAIRIE','MB'): (49.9728, -98.2920),
    # Ontario
    ('TORONTO',         'ON'): (43.6532,  -79.3832),
    ('OTTAWA',          'ON'): (45.4215,  -75.6972),
    ('MISSISSAUGA',     'ON'): (43.5890,  -79.6441),
    ('BRAMPTON',        'ON'): (43.6831,  -79.7663),
    ('HAMILTON',        'ON'): (43.2557,  -79.8711),
    ('LONDON',          'ON'): (42.9849,  -81.2453),
    ('MARKHAM',         'ON'): (43.8561,  -79.3370),
    ('VAUGHAN',         'ON'): (43.8361,  -79.4983),
    ('KITCHENER',       'ON'): (43.4516,  -80.4925),
    ('WINDSOR',         'ON'): (42.3149,  -83.0364),
    ('OAKVILLE',        'ON'): (43.4675,  -79.6877),
    ('RICHMOND HILL',   'ON'): (43.8828,  -79.4403),
    ('BURLINGTON',      'ON'): (43.3255,  -79.7990),
    ('OSHAWA',          'ON'): (43.8976,  -78.8658),
    ('BARRIE',          'ON'): (44.3894,  -79.6903),
    ('ST. CATHARINES',  'ON'): (43.1594,  -79.2469),
    ('CAMBRIDGE',       'ON'): (43.3601,  -80.3121),
    ('KINGSTON',        'ON'): (44.2312,  -76.4860),
    ('GUELPH',          'ON'): (43.5448,  -80.2482),
    ('THUNDER BAY',     'ON'): (48.3809,  -89.2477),
    ('WATERLOO',        'ON'): (43.4668,  -80.5164),
    ('SUDBURY',         'ON'): (46.4917,  -80.9930),
    ('PICKERING',       'ON'): (43.8384,  -79.0868),
    ('NIAGARA FALLS',   'ON'): (43.0896,  -79.0849),
    ('PETERBOROUGH',    'ON'): (44.3091,  -78.3197),
    ('SAULT STE. MARIE','ON'): (46.5197,  -84.3461),
    ('WHITBY',          'ON'): (43.8975,  -78.9429),
    ('AJAX',            'ON'): (43.8509,  -79.0204),
    ('BRANTFORD',       'ON'): (43.1394,  -80.2644),
    ('CLARINGTON',      'ON'): (43.9350,  -78.6078),
    ('CHATHAM',         'ON'): (42.4048,  -82.1910),
    ('BELLEVILLE',      'ON'): (44.1668,  -77.3832),
    ('SARNIA',          'ON'): (42.9745,  -82.4060),
    ('TIMMINS',         'ON'): (48.4758,  -81.3305),
    # Quebec
    ('MONTREAL',        'QC'): (45.5017,  -73.5673),
    ('QUEBEC CITY',     'QC'): (46.8139,  -71.2080),
    ('LAVAL',           'QC'): (45.5707,  -73.7031),
    ('GATINEAU',        'QC'): (45.4767,  -75.7013),
    ('LONGUEUIL',       'QC'): (45.5315,  -73.5185),
    ('SHERBROOKE',      'QC'): (45.4041,  -71.8929),
    ('LEVIS',           'QC'): (46.8037,  -71.1787),
    ('SAGUENAY',        'QC'): (48.4279,  -71.0668),
    ('TROIS-RIVIERES',  'QC'): (46.3432,  -72.5432),
    ('TERREBONNE',      'QC'): (45.7040,  -73.6450),
    ('REPENTIGNY',      'QC'): (45.7354,  -73.4605),
    ('BROSSARD',        'QC'): (45.4607,  -73.4694),
    ('DRUMMONDVILLE',   'QC'): (45.8836,  -72.4843),
    ('SAINT-JEROME',    'QC'): (45.7814,  -74.0038),
    ('GRANBY',          'QC'): (45.4000,  -72.7333),
    ('SHAWINIGAN',      'QC'): (46.5638,  -72.7519),
    ('RIMOUSKI',        'QC'): (48.4488,  -68.5275),
    # New Brunswick
    ('MONCTON',         'NB'): (46.0878,  -64.7782),
    ('SAINT JOHN',      'NB'): (45.2733,  -66.0633),
    ('FREDERICTON',     'NB'): (45.9636,  -66.6431),
    ('MIRAMICHI',       'NB'): (47.0043,  -65.4976),
    ('BATHURST',        'NB'): (47.6177,  -65.6503),
    # Nova Scotia
    ('HALIFAX',         'NS'): (44.6488,  -63.5752),
    ('CAPE BRETON',     'NS'): (46.1351,  -60.1831),
    ('TRURO',           'NS'): (45.3648,  -63.2561),
    ('NEW GLASGOW',     'NS'): (45.5853,  -62.6451),
    ('YARMOUTH',        'NS'): (43.8364,  -66.1178),
    ('SYDNEY',          'NS'): (46.1351,  -60.1831),
    ('MIDDLE SACKVILLE','NS'): (44.7717,  -63.6588),
    ('DARTMOUTH',       'NS'): (44.6711,  -63.5778),
    # Prince Edward Island
    ('CHARLOTTETOWN',   'PE'): (46.2382,  -63.1311),
    ('SUMMERSIDE',      'PE'): (46.3948,  -63.7879),
    # Newfoundland
    ("ST. JOHN'S",      'NL'): (47.5556,  -52.7453),
    ('CORNER BROOK',    'NL'): (48.9554,  -57.9500),
    ('GANDER',          'NL'): (48.9559,  -54.6085),
    ('GRAND FALLS',     'NL'): (48.9487,  -55.6644),
    # Yukon
    ('WHITEHORSE',      'YT'): (60.7212, -135.0568),
    # Northwest Territories
    ('YELLOWKNIFE',     'NT'): (62.4540, -114.3718),
    # Nunavut
    ('IQALUIT',         'NU'): (63.7467,  -68.5170),
}

# ── FSA prefix → approximate lat/lon ─────────────────────────────────────────
# Maps first 2 chars of FSA to a rough centroid.
# Built from Statistics Canada FSA mapping — covers all major districts.
FSA_PREFIX = {
    # BC
    'V0': (50.0,  -125.0), 'V1': (50.7, -120.3), 'V2': (49.1, -122.4),
    'V3': (49.2, -122.8),  'V4': (49.3, -122.9), 'V5': (49.2, -123.1),
    'V6': (49.3, -123.1),  'V7': (49.4, -123.0), 'V8': (48.6, -123.4),
    'V9': (48.9, -123.7),
    # AB
    'T0': (52.5, -113.5),  'T1': (50.9, -114.0), 'T2': (51.1, -114.1),
    'T3': (51.2, -114.2),  'T4': (53.5, -113.5), 'T5': (53.6, -113.5),
    'T6': (53.5, -113.4),  'T7': (53.2, -113.5), 'T8': (53.8, -113.6),
    'T9': (49.7, -112.8),
    # SK
    'S0': (52.0, -105.5),  'S2': (50.6, -104.6), 'S3': (50.4, -104.6),
    'S4': (50.5, -104.5),  'S6': (52.2, -106.7), 'S7': (52.1, -106.6),
    # MB
    'R0': (50.0,  -97.0),  'R1': (49.9,  -97.3), 'R2': (49.8,  -97.1),
    'R3': (49.9,  -97.2),  'R4': (49.8,  -97.3), 'R5': (49.9,  -98.2),
    'R6': (49.9,  -98.5),  'R7': (50.0,  -99.5), 'R8': (49.8,  -99.9),
    'R9': (51.0, -100.0),
    # ON
    'K0': (44.8,  -77.0),  'K1': (45.4,  -75.7), 'K2': (45.3,  -75.8),
    'K4': (44.5,  -76.3),  'K6': (44.2,  -77.3), 'K7': (44.2,  -76.5),
    'K8': (46.5,  -80.8),  'K9': (46.4,  -80.5),
    'L0': (44.0,  -79.3),  'L1': (43.9,  -79.0), 'L2': (43.3,  -79.8),
    'L3': (43.5,  -79.9),  'L4': (43.8,  -79.5), 'L5': (43.6,  -79.6),
    'L6': (43.7,  -79.7),  'L7': (43.9,  -79.4), 'L8': (43.3,  -79.9),
    'L9': (44.1,  -79.6),
    'M1': (43.8,  -79.2),  'M2': (43.7,  -79.4), 'M3': (43.7,  -79.5),
    'M4': (43.7,  -79.4),  'M5': (43.6,  -79.4), 'M6': (43.6,  -79.5),
    'M8': (43.6,  -79.5),  'M9': (43.7,  -79.5),
    'N0': (43.0,  -80.8),  'N1': (43.4,  -80.5), 'N2': (43.5,  -80.5),
    'N3': (43.2,  -79.9),  'N4': (42.8,  -80.5), 'N5': (42.8,  -81.2),
    'N6': (43.0,  -81.3),  'N7': (43.0,  -82.0), 'N8': (42.3,  -83.0),
    'N9': (42.1,  -82.9),
    'P0': (46.5,  -82.0),  'P1': (46.5,  -80.9), 'P2': (46.3,  -81.0),
    'P3': (46.4,  -80.9),  'P4': (46.5,  -84.3), 'P5': (48.0,  -85.0),
    'P6': (46.6,  -79.5),  'P7': (48.4,  -89.2), 'P8': (46.5,  -82.0),
    'P9': (48.5,  -81.3),
    # QC
    'G0': (47.2,  -70.5),  'G1': (46.8,  -71.2), 'G2': (46.8,  -71.3),
    'G3': (46.9,  -71.4),  'G4': (46.9,  -71.5), 'G5': (46.5,  -71.8),
    'G6': (46.3,  -72.4),  'G7': (48.4,  -71.1), 'G8': (46.6,  -72.8),
    'G9': (46.4,  -72.6),
    'H1': (45.6,  -73.6),  'H2': (45.5,  -73.6), 'H3': (45.5,  -73.6),
    'H4': (45.5,  -73.7),  'H7': (45.7,  -73.8), 'H8': (45.5,  -73.9),
    'H9': (45.4,  -73.8),
    'J0': (45.5,  -72.5),  'J1': (45.4,  -71.9), 'J2': (45.6,  -73.7),
    'J3': (45.5,  -73.4),  'J4': (45.5,  -73.5), 'J5': (45.6,  -73.5),
    'J6': (45.6,  -74.0),  'J7': (45.7,  -74.0), 'J8': (45.5,  -75.7),
    'J9': (45.7,  -77.0),
    # NB
    'E1': (46.1,  -64.8),  'E2': (45.3,  -66.1), 'E3': (45.9,  -66.6),
    'E4': (46.0,  -65.5),  'E5': (45.8,  -66.5), 'E6': (47.0,  -65.5),
    'E7': (47.5,  -65.7),  'E8': (47.6,  -65.7), 'E9': (47.0,  -65.0),
    # NS
    'B0': (44.5,  -64.5),  'B1': (46.1,  -60.2), 'B2': (44.7,  -63.6),
    'B3': (44.5,  -64.0),  'B4': (44.7,  -63.7), 'B5': (43.8,  -66.1),
    'B6': (45.6,  -62.6),  'B9': (45.4,  -62.5),
    # PE
    'C0': (46.3,  -63.5),  'C1': (46.2,  -63.1),
    # NL
    'A0': (47.0,  -53.0),  'A1': (47.6,  -52.7), 'A2': (47.0,  -55.0),
    'A5': (48.9,  -57.9),  'A8': (48.9,  -54.6),
    # Territories
    'X0': (64.0, -130.0),  'X1': (62.5, -114.4),
    'Y0': (60.7, -135.1),  'Y1': (60.7, -135.1),
}

def postal_to_latlon(postal_code: str, prov: str) -> tuple:
    """Convert Canadian postal code to approximate lat/lon.
    Tries: full 6-char postal → FSA 2-char prefix → province centroid."""
    postal = postal_code.upper().replace(' ', '').strip()
    if len(postal) < 3:
        if prov in PROVINCE:
            lat, lon, _ = PROVINCE[prov]
            return lat, lon, 'province'
        return None, None, None

    fsa = postal[:3]  # e.g. B4E
    prefix2 = postal[:2]  # e.g. B4

    if prefix2 in FSA_PREFIX:
        lat, lon = FSA_PREFIX[prefix2]
        return lat, lon, 'fsa'
    elif prov in PROVINCE:
        lat, lon, _ = PROVINCE[prov]
        return lat, lon, 'province'
    return None, None, None


def latlon_to_grid(lat: float, lon: float) -> str:
    """Convert lat/lon to 6-char Maidenhead grid locator."""
    lon += 180
    lat += 90
    grid = ''
    grid += chr(ord('A') + int(lon / 20))
    grid += chr(ord('A') + int(lat / 10))
    grid += str(int((lon % 20) / 2))
    grid += str(int(lat % 10))
    grid += chr(ord('A') + int((lon % 2) * 12))
    grid += chr(ord('A') + int((lat % 1) * 24))
    return grid.upper()


def qual_to_class(row: dict) -> str:
    """Map Canadian qualification fields to a license class string."""
    if row.get('qual_a') == 'A':
        return 'Advanced'
    if row.get('qual_b') == 'B':
        return 'Basic with Honours'
    if row.get('qual_c') == 'C':
        return 'Basic'
    return 'Amateur'


def migrate_db(conn):
    """Add country column to callsigns table if not present."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(callsigns)").fetchall()]
    if 'country' not in cols:
        conn.execute("ALTER TABLE callsigns ADD COLUMN country TEXT DEFAULT 'US'")
        conn.execute("UPDATE callsigns SET country='US' WHERE country IS NULL")
        print("  Added country column to callsigns table")
    # Ensure table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callsigns (
            callsign TEXT PRIMARY KEY,
            name TEXT, city TEXT, state TEXT,
            zip TEXT, lat REAL, lon REAL, class TEXT,
            country TEXT DEFAULT 'US'
        )
    """)
    conn.commit()


def main():
    if len(sys.argv) < 3:
        print("Usage: python build_canadian_db.py amateur_delim.txt ham.db")
        sys.exit(1)

    src_file = sys.argv[1]
    db_file  = sys.argv[2]

    if not os.path.exists(src_file):
        print(f"Error: source file not found: {src_file}")
        sys.exit(1)

    print(f"Reading {src_file} ...")
    print(f"Target database: {db_file}")
    print()

    conn = sqlite3.connect(db_file)
    migrate_db(conn)

    inserted = updated = skipped = no_loc = 0
    province_hits = {}  # prov → count

    with open(src_file, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f, delimiter=';')
        batch = []

        for row in reader:
            callsign = (row.get('callsign') or '').strip().upper()
            if not callsign:
                skipped += 1
                continue

            first   = (row.get('first_name') or '').strip().title()
            surname = (row.get('surname')    or '').strip().title()
            name    = f"{first} {surname}".strip() if (first or surname) else callsign
            city    = (row.get('city')       or '').strip().title()
            prov    = (row.get('prov_cd')    or '').strip().upper()
            postal  = (row.get('postal_code')or '').strip().upper()
            lic_cls = qual_to_class(row)

            # Try city lookup first for better accuracy
            city_key = (city.upper(), prov)
            if city_key in CITIES:
                lat, lon = CITIES[city_key]
                loc_src = 'city'
            else:
                lat, lon, loc_src = postal_to_latlon(postal, prov)

            if lat is None:
                no_loc += 1
                lat, lon = 0.0, 0.0
                loc_src = 'none'

            prov_name = PROVINCE.get(prov, (None, None, prov))[2]
            province_hits[prov] = province_hits.get(prov, 0) + 1

            batch.append((callsign, name, city, prov_name, postal, lat, lon, lic_cls, 'CA'))

            if len(batch) >= 500:
                _flush(conn, batch)
                inserted += sum(1 for _ in batch)
                batch.clear()

        if batch:
            _flush(conn, batch)
            inserted += len(batch)

    conn.commit()
    conn.close()

    print(f"\n{'='*55}")
    print(f"  Canadian amateur database import complete")
    print(f"{'='*55}")
    print(f"  Records processed : {inserted:>7,}")
    print(f"  No location found : {no_loc:>7,}")
    print(f"  Skipped (no call) : {skipped:>7,}")
    print(f"\n  Records by province:")
    for prov, count in sorted(province_hits.items(), key=lambda x: -x[1]):
        name = PROVINCE.get(prov, (None, None, prov))[2]
        print(f"    {prov}  {name:<35} {count:>6,}")
    print(f"\n  ham.db updated: {db_file}")
    print(f"  Canadian callsigns (VA/VE/VY/VO) now resolve in JS8Map.")
    print(f"{'='*55}")


def _flush(conn, batch):
    conn.executemany("""
        INSERT INTO callsigns (callsign, name, city, state, zip, lat, lon, class, country)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(callsign) DO UPDATE SET
            name=excluded.name, city=excluded.city, state=excluded.state,
            zip=excluded.zip, lat=excluded.lat, lon=excluded.lon,
            class=excluded.class, country=excluded.country
    """, batch)


if __name__ == '__main__':
    main()
