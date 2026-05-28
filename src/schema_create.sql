-- =============================================================================
-- Toast Orders Full Schema
-- Captures the complete Toast API JSON structure
-- =============================================================================

-- -----------------------------------------------------------------------------
-- TIER 1: Order Header
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders_head (
    order_guid                  VARCHAR(64)     PRIMARY KEY,
    location_id                 VARCHAR(64),
    external_id                 VARCHAR(128),
    order_number                VARCHAR(32),

    -- Dates
    fire_date                   TIMESTAMPTZ,
    promised_date               TIMESTAMPTZ,
    created_date                TIMESTAMPTZ,
    closed_date                 TIMESTAMPTZ,
    paid_date                   TIMESTAMPTZ,
    modified_date               TIMESTAMPTZ,
    deleted_date                TIMESTAMPTZ,
    void_date                   TIMESTAMPTZ,
    void_business_date          INTEGER,
    estimated_fulfillment_date  TIMESTAMPTZ,
    business_date               INTEGER,

    -- Order details
    required_prep_time          VARCHAR(64),
    number_of_guests            INTEGER,
    approval_status             VARCHAR(32),
    source                      VARCHAR(64),
    pricing_features            TEXT[],
    channel_guid                VARCHAR(64),
    duration                    INTEGER,            -- order-level duration (seconds)

    -- Status flags
    deleted                     BOOLEAN             DEFAULT FALSE,
    voided                      BOOLEAN             DEFAULT FALSE,
    excess_food                 BOOLEAN             DEFAULT FALSE,
    created_in_test_mode        BOOLEAN             DEFAULT FALSE,

    -- Reference GUIDs
    dining_option_guid          VARCHAR(64),
    service_area_guid           VARCHAR(64),        -- top-level serviceArea on order
    table_guid                  VARCHAR(64),        -- top-level table on order
    restaurant_service_guid     VARCHAR(64),
    revenue_center_guid         VARCHAR(64),
    server_guid                 VARCHAR(64),

    -- Curbside pickup
    curbside_guid               VARCHAR(64),
    curbside_transport_color    VARCHAR(64),
    curbside_transport_desc     VARCHAR(256),
    curbside_notes              TEXT,

    -- Device tracking
    created_device_id           VARCHAR(64),
    last_modified_device_id     VARCHAR(64)
);

-- -----------------------------------------------------------------------------
-- TIER 1b: Delivery Info  (one-to-one with orders_head)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_delivery_info (
    order_guid              VARCHAR(64)     PRIMARY KEY
                                REFERENCES orders_head(order_guid) ON DELETE CASCADE,
    address1                VARCHAR(256),
    address2                VARCHAR(256),
    city                    VARCHAR(128),
    state                   VARCHAR(32),
    zip_code                VARCHAR(20),
    country                 VARCHAR(64),
    administrative_area     VARCHAR(128),
    latitude                NUMERIC(10, 7),
    longitude               NUMERIC(10, 7),
    notes                   TEXT,
    delivery_state          VARCHAR(64),
    dispatched_date         TIMESTAMPTZ,
    delivered_date          TIMESTAMPTZ,
    delivery_employee_guid  VARCHAR(64)     -- deliveryInfo.deliveryEmployee.guid
);

-- -----------------------------------------------------------------------------
-- TIER 2: Checks
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_checks (
    check_guid              VARCHAR(64)     PRIMARY KEY,
    order_guid              VARCHAR(64)     NOT NULL
                                REFERENCES orders_head(order_guid) ON DELETE CASCADE,
    external_id             VARCHAR(128),
    display_number          VARCHAR(32),

    -- Status & amounts
    payment_status          VARCHAR(32),
    tax_exempt              BOOLEAN         DEFAULT FALSE,
    tax_exemption_account   VARCHAR(128),
    total_amount            NUMERIC(12, 4),
    tax_amount              NUMERIC(12, 4),
    net_amount              NUMERIC(12, 4),

    -- Tab & customer
    tab_name                VARCHAR(128),
    customer_guid           VARCHAR(64),
    customer_first          VARCHAR(128),
    customer_last           VARCHAR(128),
    customer_phone          VARCHAR(32),
    customer_phone_country  VARCHAR(8),
    customer_email          VARCHAR(256),

    -- Loyalty
    loyalty_guid            VARCHAR(64),
    loyalty_identifier      VARCHAR(256),
    loyalty_vendor          VARCHAR(64),

    -- Dates
    opened_date             TIMESTAMPTZ,
    closed_date             TIMESTAMPTZ,
    paid_date               TIMESTAMPTZ,
    void_date               TIMESTAMPTZ,
    void_business_date      INTEGER,
    created_date            TIMESTAMPTZ,
    modified_date           TIMESTAMPTZ,
    deleted_date            TIMESTAMPTZ,
    duration                INTEGER,            -- check-level duration (seconds)

    -- Flags
    voided                  BOOLEAN         DEFAULT FALSE,
    deleted                 BOOLEAN         DEFAULT FALSE,

    -- References
    opened_by_guid          VARCHAR(64),    -- openedBy.guid (was incorrectly VARCHAR raw)
    created_device_id       VARCHAR(64),
    last_modified_device_id VARCHAR(64)
);

-- -----------------------------------------------------------------------------
-- TIER 2b: Payments
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS check_payments (
    payment_guid                VARCHAR(64)     PRIMARY KEY,
    check_guid                  VARCHAR(64)     NOT NULL
                                    REFERENCES order_checks(check_guid) ON DELETE CASCADE,
    order_guid                  VARCHAR(64),
    external_id                 VARCHAR(128),

    -- Payment details
    type                        VARCHAR(32),
    payment_status              VARCHAR(32),
    amount                      NUMERIC(12, 4),
    tip_amount                  NUMERIC(12, 4),
    amount_tendered             NUMERIC(12, 4),
    original_processing_fee     NUMERIC(12, 4),
    surcharge_amount            NUMERIC(12, 4),
    mca_repayment_amount        NUMERIC(12, 4),
    refund_status               VARCHAR(32),

    -- Card info
    card_type                   VARCHAR(32),
    card_entry_mode             VARCHAR(32),
    last_4_digits               VARCHAR(4),
    first_6_digits              VARCHAR(6),
    card_processor_type         VARCHAR(64),
    card_payment_id             VARCHAR(128),

    -- Refund details
    refund_amount               NUMERIC(12, 4),
    tip_refund_amount           NUMERIC(12, 4),
    refund_date                 TIMESTAMPTZ,
    refund_business_date        INTEGER,
    refund_transaction_guid     VARCHAR(64),

    -- Void info
    void_date                   TIMESTAMPTZ,
    void_business_date          INTEGER,
    void_reason_guid            VARCHAR(64),
    void_user_guid              VARCHAR(64),
    void_approver_guid          VARCHAR(64),

    -- References
    server_guid                 VARCHAR(64),
    cash_drawer_guid            VARCHAR(64),
    house_account_guid          VARCHAR(64),
    other_payment_guid          VARCHAR(64),

    -- Dates & flags
    paid_date                   TIMESTAMPTZ,
    paid_business_date          INTEGER,
    is_processed_offline        BOOLEAN         DEFAULT FALSE,

    -- Device / transaction references
    created_device_id           VARCHAR(64),
    last_modified_device_id     VARCHAR(64),
    tender_transaction_guid     VARCHAR(64),
    network_transaction_id      VARCHAR(128)
);

-- -----------------------------------------------------------------------------
-- TIER 2c: Applied Discounts on Checks
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS check_discounts (
    discount_guid           VARCHAR(64)     PRIMARY KEY,
    check_guid              VARCHAR(64)     NOT NULL
                                REFERENCES order_checks(check_guid) ON DELETE CASCADE,
    external_id             VARCHAR(128),
    discount_amount         NUMERIC(12, 4),
    non_tax_discount_amount NUMERIC(12, 4),
    discount_name           VARCHAR(256),
    discount_type           VARCHAR(64),
    discount_percent        NUMERIC(8, 4),
    discount_ref_guid       VARCHAR(64),    -- discount.guid
    processing_state        VARCHAR(64),
    applied_promo_code      VARCHAR(128),
    approver_guid           VARCHAR(64)
);

-- -----------------------------------------------------------------------------
-- TIER 2d: Applied Service Charges on Checks  (NEW)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS check_service_charges (
    charge_guid             VARCHAR(64)     PRIMARY KEY,
    check_guid              VARCHAR(64)     NOT NULL
                                REFERENCES order_checks(check_guid) ON DELETE CASCADE,
    external_id             VARCHAR(128),
    name                    VARCHAR(256),
    charge_amount           NUMERIC(12, 4),
    charge_type             VARCHAR(32),        -- FIXED, PERCENT
    service_charge_guid     VARCHAR(64),        -- serviceCharge.guid reference
    service_charge_category VARCHAR(64),        -- SERVICE_CHARGE, GRATUITY, etc.
    payment_guid            VARCHAR(64),        -- which payment this charge ties to

    -- Applicability flags
    delivery                BOOLEAN         DEFAULT FALSE,
    takeout                 BOOLEAN         DEFAULT FALSE,
    dine_in                 BOOLEAN         DEFAULT FALSE,
    gratuity                BOOLEAN         DEFAULT FALSE,
    taxable                 BOOLEAN         DEFAULT FALSE,

    -- Pricing rule
    service_charge_calculation VARCHAR(32),     -- PRE_DISCOUNT, POST_DISCOUNT

    -- Refund
    refund_amount           NUMERIC(12, 4),
    tax_refund_amount       NUMERIC(12, 4),
    refund_transaction_guid VARCHAR(64)
);

-- -----------------------------------------------------------------------------
-- TIER 2e: Applied Taxes on Service Charges  (NEW)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_charge_taxes (
    applied_tax_guid            VARCHAR(64)     PRIMARY KEY,
    charge_guid                 VARCHAR(64)     NOT NULL
                                    REFERENCES check_service_charges(charge_guid) ON DELETE CASCADE,
    tax_rate_guid               VARCHAR(64),
    tax_name                    VARCHAR(128),
    rate                        NUMERIC(8, 6),
    tax_amount                  NUMERIC(12, 4),
    type                        VARCHAR(32),
    jurisdiction_type           VARCHAR(64),
    jurisdiction                VARCHAR(128),
    display_name                VARCHAR(128),
    facilitator_collect_remit   BOOLEAN         DEFAULT FALSE
);

-- -----------------------------------------------------------------------------
-- TIER 3: Item Selections
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_items (
    selection_guid              VARCHAR(64)     PRIMARY KEY,
    check_guid                  VARCHAR(64)     NOT NULL
                                    REFERENCES order_checks(check_guid) ON DELETE CASCADE,
    external_id                 VARCHAR(128),

    -- Item references
    item_guid                   VARCHAR(64),
    item_multi_location_id      VARCHAR(64),
    item_group_guid             VARCHAR(64),
    item_group_multi_loc_id     VARCHAR(64),
    sales_category_guid         VARCHAR(64),
    option_group_guid           VARCHAR(64),
    option_group_multi_loc_id   VARCHAR(64),
    pre_modifier_guid           VARCHAR(64),    -- preModifier.guid (NEW)
    pre_modifier_multi_loc_id   VARCHAR(64),    -- preModifier.multiLocationId (NEW)
    dining_option_guid          VARCHAR(64),
    split_origin_guid           VARCHAR(64),    -- splitOrigin.guid (NEW)

    -- Display & classification
    item_name                   VARCHAR(512),
    selection_type              VARCHAR(32),
    unit_of_measure             VARCHAR(32),
    plu                         VARCHAR(64),
    premodifier_plu             VARCHAR(64),    -- NEW
    sales_category_plu          VARCHAR(64),    -- NEW
    seat_number                 INTEGER,

    -- Pricing
    quantity                    NUMERIC(10, 4),
    unit_price                  NUMERIC(12, 4), -- receiptLinePrice
    net_price                   NUMERIC(12, 4), -- price
    pre_discount_price          NUMERIC(12, 4),
    open_price_amount           NUMERIC(12, 4), -- NEW
    external_price_amount       NUMERIC(12, 4), -- NEW
    tax_amount                  NUMERIC(12, 4),
    tax_inclusion               VARCHAR(32),
    option_group_pricing_mode   VARCHAR(32),    -- NEW (at selection level too)

    -- Refund details (NEW)
    refund_amount               NUMERIC(12, 4),
    tax_refund_amount           NUMERIC(12, 4),
    refund_transaction_guid     VARCHAR(64),

    -- Status
    fulfillment_status          VARCHAR(32),
    voided                      BOOLEAN         DEFAULT FALSE,
    deferred                    BOOLEAN         DEFAULT FALSE,

    -- Dates
    created_date                TIMESTAMPTZ,
    modified_date               TIMESTAMPTZ,
    void_date                   TIMESTAMPTZ,
    void_business_date          INTEGER,
    void_reason                 VARCHAR(64)     -- voidReason.guid
);

-- -----------------------------------------------------------------------------
-- TIER 3b: Applied Taxes per Selection
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_applied_taxes (
    applied_tax_guid            VARCHAR(64)     PRIMARY KEY,
    selection_guid              VARCHAR(64)     NOT NULL
                                    REFERENCES order_items(selection_guid) ON DELETE CASCADE,
    tax_rate_guid               VARCHAR(64),
    tax_name                    VARCHAR(128),
    rate                        NUMERIC(8, 6),
    tax_amount                  NUMERIC(12, 4),
    type                        VARCHAR(32),
    jurisdiction_type           VARCHAR(64),
    jurisdiction                VARCHAR(128),
    display_name                VARCHAR(128),
    facilitator_collect_remit   BOOLEAN         DEFAULT FALSE
);

-- -----------------------------------------------------------------------------
-- TIER 3c: Applied Discounts on Selections
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_discounts (
    discount_guid           VARCHAR(64)     PRIMARY KEY,
    selection_guid          VARCHAR(64)     NOT NULL
                                REFERENCES order_items(selection_guid) ON DELETE CASCADE,
    external_id             VARCHAR(128),
    discount_amount         NUMERIC(12, 4),
    non_tax_discount_amount NUMERIC(12, 4),
    discount_name           VARCHAR(256),
    discount_type           VARCHAR(64),
    discount_percent        NUMERIC(8, 4),
    discount_ref_guid       VARCHAR(64),
    processing_state        VARCHAR(64),
    applied_promo_code      VARCHAR(128),
    approver_guid           VARCHAR(64)
);

-- -----------------------------------------------------------------------------
-- TIER 4: Item Modifiers
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_modifiers (
    modifier_guid               VARCHAR(64)     PRIMARY KEY,
    selection_guid              VARCHAR(64)     NOT NULL
                                    REFERENCES order_items(selection_guid) ON DELETE CASCADE,
    external_id                 VARCHAR(128),

    -- Item references
    item_guid                   VARCHAR(64),
    item_multi_location_id      VARCHAR(64),
    option_group_guid           VARCHAR(64),
    option_group_multi_loc      VARCHAR(64),
    pre_modifier_guid           VARCHAR(64),    -- NEW
    pre_modifier_multi_loc_id   VARCHAR(64),    -- NEW
    dining_option_guid          VARCHAR(64),
    split_origin_guid           VARCHAR(64),    -- NEW

    -- Display
    mod_name                    VARCHAR(512),
    selection_type              VARCHAR(32),
    unit_of_measure             VARCHAR(32),
    plu                         VARCHAR(64),
    premodifier_plu             VARCHAR(64),    -- NEW
    sales_category_plu          VARCHAR(64),    -- NEW
    seat_number                 INTEGER,

    -- Pricing
    quantity                    NUMERIC(10, 4),
    mod_unit_price              NUMERIC(12, 4),
    mod_net_price               NUMERIC(12, 4),
    pre_discount_price          NUMERIC(12, 4),
    open_price_amount           NUMERIC(12, 4), -- NEW
    external_price_amount       NUMERIC(12, 4), -- NEW
    tax_amount                  NUMERIC(12, 4),
    tax_inclusion               VARCHAR(32),
    option_group_pricing_mode   VARCHAR(32),

    -- Refund (NEW)
    refund_amount               NUMERIC(12, 4),
    tax_refund_amount           NUMERIC(12, 4),
    refund_transaction_guid     VARCHAR(64),

    -- Status
    fulfillment_status          VARCHAR(32),
    voided                      BOOLEAN         DEFAULT FALSE,
    deferred                    BOOLEAN         DEFAULT FALSE,

    -- Dates
    created_date                TIMESTAMPTZ,
    modified_date               TIMESTAMPTZ,
    void_date                   TIMESTAMPTZ,
    void_business_date          INTEGER,
    void_reason                 VARCHAR(64)
);

-- =============================================================================
-- Indexes
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_orders_head_location          ON orders_head(location_id);
CREATE INDEX IF NOT EXISTS idx_orders_head_business_date     ON orders_head(business_date);
CREATE INDEX IF NOT EXISTS idx_orders_head_closed_date       ON orders_head(closed_date);
CREATE INDEX IF NOT EXISTS idx_orders_head_server            ON orders_head(server_guid);

CREATE INDEX IF NOT EXISTS idx_order_checks_order            ON order_checks(order_guid);
CREATE INDEX IF NOT EXISTS idx_order_checks_paid_date        ON order_checks(paid_date);
CREATE INDEX IF NOT EXISTS idx_order_checks_customer         ON order_checks(customer_guid);

CREATE INDEX IF NOT EXISTS idx_check_payments_check          ON check_payments(check_guid);
CREATE INDEX IF NOT EXISTS idx_check_payments_order          ON check_payments(order_guid);
CREATE INDEX IF NOT EXISTS idx_check_payments_paid_date      ON check_payments(paid_date);

CREATE INDEX IF NOT EXISTS idx_check_service_charges_check   ON check_service_charges(check_guid);
CREATE INDEX IF NOT EXISTS idx_check_discounts_check         ON check_discounts(check_guid);

CREATE INDEX IF NOT EXISTS idx_order_items_check             ON order_items(check_guid);
CREATE INDEX IF NOT EXISTS idx_order_items_item              ON order_items(item_guid);
CREATE INDEX IF NOT EXISTS idx_order_items_sales_cat         ON order_items(sales_category_guid);
CREATE INDEX IF NOT EXISTS idx_order_items_voided            ON order_items(voided);

CREATE INDEX IF NOT EXISTS idx_item_taxes_selection          ON item_applied_taxes(selection_guid);
CREATE INDEX IF NOT EXISTS idx_item_discounts_selection      ON item_discounts(selection_guid);
CREATE INDEX IF NOT EXISTS idx_item_modifiers_selection      ON item_modifiers(selection_guid);
CREATE INDEX IF NOT EXISTS idx_service_charge_taxes_charge   ON service_charge_taxes(charge_guid);
