-- Typed, cleaned tables. Python validation is the main gate; the CHECK constraints
-- here are the last line of defence, so garbage cannot land even if a validation rule
-- is missing or buggy.

-- Conformed region dimension shared by all sources. The codes are the official
-- Austrian Bundesland codes, which E-Control and Statistik Austria both use directly.
CREATE TABLE IF NOT EXISTS staging.bundesland (
    bundesland_code smallint PRIMARY KEY,
    name_de         text NOT NULL UNIQUE,
    name_en         text NOT NULL UNIQUE,
    iso_3166_2      text NOT NULL UNIQUE,
    CONSTRAINT bundesland_code_chk CHECK (bundesland_code BETWEEN 1 AND 9)
);

-- GeoSphere weather stations, from the dataset metadata endpoint.
CREATE TABLE IF NOT EXISTS staging.weather_station (
    station_id      integer PRIMARY KEY,
    station_name    text          NOT NULL,
    bundesland_code smallint      NOT NULL REFERENCES staging.bundesland (bundesland_code),
    latitude        numeric(8, 5) NOT NULL,
    longitude       numeric(8, 5) NOT NULL,
    altitude_m      numeric(6, 1),
    valid_from      date,
    valid_to        date,
    is_active       boolean       NOT NULL,
    updated_at      timestamptz   NOT NULL DEFAULT now()
);

-- Daily weather per station. Natural key (station_id, obs_date) makes the load an
-- upsert: re-fetching a day overwrites it with the latest (possibly revised) values.
CREATE TABLE IF NOT EXISTS staging.weather_daily (
    station_id       integer      NOT NULL REFERENCES staging.weather_station (station_id),
    obs_date         date         NOT NULL,
    temp_mean_c      numeric(4, 1),
    temp_max_c       numeric(4, 1),
    temp_min_c       numeric(4, 1),
    precip_mm        numeric(5, 1),
    sunshine_h       numeric(3, 1),
    min_quality_flag smallint,
    raw_id           bigint       NOT NULL REFERENCES raw.api_response (raw_id),
    loaded_at        timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, obs_date),
    CONSTRAINT weather_daily_temp_range_chk CHECK (
        (temp_mean_c IS NULL OR temp_mean_c BETWEEN -60 AND 50)
        AND (temp_max_c IS NULL OR temp_max_c BETWEEN -60 AND 50)
        AND (temp_min_c IS NULL OR temp_min_c BETWEEN -60 AND 50)
    ),
    CONSTRAINT weather_daily_temp_order_chk CHECK (
        temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c <= temp_max_c
    ),
    CONSTRAINT weather_daily_precip_chk CHECK (precip_mm IS NULL OR precip_mm BETWEEN 0 AND 500),
    CONSTRAINT weather_daily_sunshine_chk CHECK (sunshine_h IS NULL OR sunshine_h BETWEEN 0 AND 24)
);

-- The PK leads with station_id, which suits "one station over time". The mart views
-- aggregate "all stations for a date range", which this index serves.
CREATE INDEX IF NOT EXISTS weather_daily_obs_date_idx ON staging.weather_daily (obs_date);

-- Daily snapshot of the cheapest fuel prices per Bundesland, as listed by E-Control.
-- A snapshot is replaced as a whole (delete plus insert for one date, region and fuel
-- type, in one transaction) rather than upserted row by row: if a station drops off the
-- list in a second run on the same day, a row-level upsert would leave it behind.
CREATE TABLE IF NOT EXISTS staging.fuel_price (
    snapshot_date   date          NOT NULL,
    bundesland_code smallint      NOT NULL REFERENCES staging.bundesland (bundesland_code),
    fuel_type       text          NOT NULL,
    station_id      integer       NOT NULL,
    station_name    text          NOT NULL,
    postal_code     text,
    city            text,
    latitude        numeric(8, 5),
    longitude       numeric(8, 5),
    price_eur       numeric(5, 3) NOT NULL,
    list_position   smallint      NOT NULL,
    fetched_at      timestamptz   NOT NULL,
    raw_id          bigint        NOT NULL REFERENCES raw.api_response (raw_id),
    loaded_at       timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, bundesland_code, fuel_type, station_id),
    CONSTRAINT fuel_price_fuel_type_chk CHECK (fuel_type IN ('DIE', 'SUP')),
    CONSTRAINT fuel_price_price_chk CHECK (price_eur BETWEEN 0.5 AND 5.0),
    CONSTRAINT fuel_price_position_chk CHECK (list_position >= 1)
);

-- Population per Bundesland on 1 January of each year (Statistik Austria).
CREATE TABLE IF NOT EXISTS staging.population (
    bundesland_code smallint    NOT NULL REFERENCES staging.bundesland (bundesland_code),
    reference_date  date        NOT NULL,
    population      integer     NOT NULL,
    raw_id          bigint      NOT NULL REFERENCES raw.api_response (raw_id),
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bundesland_code, reference_date),
    CONSTRAINT population_positive_chk CHECK (population > 0),
    CONSTRAINT population_jan_first_chk
        CHECK (EXTRACT(MONTH FROM reference_date) = 1 AND EXTRACT(DAY FROM reference_date) = 1)
);
