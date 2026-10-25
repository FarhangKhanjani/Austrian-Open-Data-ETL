-- Static reference data. ON CONFLICT DO UPDATE keeps this file safe to re-run and lets a
-- corrected name be fixed by editing this file.
INSERT INTO staging.bundesland (bundesland_code, name_de, name_en, iso_3166_2) VALUES
    (1, 'Burgenland',       'Burgenland',    'AT-1'),
    (2, 'Kärnten',          'Carinthia',     'AT-2'),
    (3, 'Niederösterreich', 'Lower Austria', 'AT-3'),
    (4, 'Oberösterreich',   'Upper Austria', 'AT-4'),
    (5, 'Salzburg',         'Salzburg',      'AT-5'),
    (6, 'Steiermark',       'Styria',        'AT-6'),
    (7, 'Tirol',            'Tyrol',         'AT-7'),
    (8, 'Vorarlberg',       'Vorarlberg',    'AT-8'),
    (9, 'Wien',             'Vienna',        'AT-9')
ON CONFLICT (bundesland_code) DO UPDATE
SET name_de    = EXCLUDED.name_de,
    name_en    = EXCLUDED.name_en,
    iso_3166_2 = EXCLUDED.iso_3166_2;
