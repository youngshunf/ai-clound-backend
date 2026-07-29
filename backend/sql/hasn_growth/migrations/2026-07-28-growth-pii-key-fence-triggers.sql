-- 数据库最终门禁：所有 Growth PII 密文和版本化 HMAC 禁止低于单例栅栏。

SET search_path TO hasn_growth, public;

CREATE OR REPLACE FUNCTION enforce_growth_pii_key_state_monotonic()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Growth PII 密钥版本栅栏禁止删除'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.min_encryption_write_version < OLD.min_encryption_write_version
       OR NEW.min_hmac_write_version < OLD.min_hmac_write_version THEN
        RAISE EXCEPTION 'Growth PII 密钥版本栅栏禁止降级'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_growth_pii_key_write_fence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    fence_encryption_version integer;
BEGIN
    SELECT min_encryption_write_version
    INTO STRICT fence_encryption_version
    FROM hasn_growth.growth_pii_key_state
    WHERE id = 1
    FOR SHARE;

    IF NEW.encryption_key_version < fence_encryption_version THEN
        RAISE EXCEPTION 'Growth PII 加密密钥版本低于写入栅栏'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_growth_pii_channel_key_write_fence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    fence_encryption_version integer;
    fence_hmac_version integer;
BEGIN
    SELECT
        min_encryption_write_version,
        min_hmac_write_version
    INTO STRICT
        fence_encryption_version,
        fence_hmac_version
    FROM hasn_growth.growth_pii_key_state
    WHERE id = 1
    FOR SHARE;

    IF NEW.encryption_key_version < fence_encryption_version
       OR NEW.hash_key_version < fence_hmac_version THEN
        RAISE EXCEPTION 'Growth PII 渠道密钥版本低于写入栅栏'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_growth_pii_optout_key_write_fence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    fence_hmac_version integer;
BEGIN
    SELECT min_hmac_write_version
    INTO STRICT fence_hmac_version
    FROM hasn_growth.growth_pii_key_state
    WHERE id = 1
    FOR SHARE;

    IF NEW.address_hmac IS NOT NULL
       AND NEW.hash_key_version < fence_hmac_version THEN
        RAISE EXCEPTION 'Growth PII 退订密钥版本低于写入栅栏'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_growth_private_profile_key_fence
BEFORE INSERT OR UPDATE ON contact_private_profile
FOR EACH ROW EXECUTE FUNCTION enforce_growth_pii_key_write_fence();

CREATE OR REPLACE TRIGGER trg_growth_pii_key_state_monotonic
BEFORE UPDATE OR DELETE ON growth_pii_key_state
FOR EACH ROW EXECUTE FUNCTION enforce_growth_pii_key_state_monotonic();

CREATE OR REPLACE TRIGGER trg_growth_contact_channel_key_fence
BEFORE INSERT OR UPDATE ON contact_channel
FOR EACH ROW EXECUTE FUNCTION enforce_growth_pii_channel_key_write_fence();

CREATE OR REPLACE TRIGGER trg_growth_optout_key_fence
BEFORE INSERT OR UPDATE ON optout_record
FOR EACH ROW EXECUTE FUNCTION enforce_growth_pii_optout_key_write_fence();
