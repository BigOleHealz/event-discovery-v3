"""Store the category taxonomy and source-label aliases in Postgres (Phase 5b)."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260926_0012"
down_revision: str | None = "20260926_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE category (
            id TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            name TEXT NOT NULL UNIQUE CHECK (length(btrim(name)) > 0),
            parent_id TEXT REFERENCES category(id),
            CHECK (id <> parent_id)
        );
        CREATE TABLE category_alias (
            alias TEXT PRIMARY KEY CHECK (alias = lower(btrim(alias)) AND alias <> ''),
            category_id TEXT NOT NULL REFERENCES category(id) ON DELETE CASCADE
        );
        CREATE FUNCTION check_category_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                WITH RECURSIVE ancestors AS (
                    SELECT id, parent_id FROM category WHERE id = NEW.parent_id
                    UNION
                    SELECT c.id, c.parent_id FROM category c JOIN ancestors a ON c.id = a.parent_id
                ) SELECT 1 FROM ancestors WHERE id = NEW.id
            ) THEN
                RAISE EXCEPTION 'category hierarchy cannot contain a cycle'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER category_cycle BEFORE INSERT OR UPDATE ON category
            FOR EACH ROW EXECUTE FUNCTION check_category_cycle();

        INSERT INTO category (id, name) VALUES
            ('music', 'Music'), ('arts', 'Arts & Culture'),
            ('food-and-drink', 'Food & Drink'), ('community', 'Community'),
            ('science-and-tech', 'Science & Technology'), ('business', 'Business'),
            ('sports-and-fitness', 'Sports & Fitness'), ('other', 'Other');
        INSERT INTO category (id, name, parent_id) VALUES
            ('jazz', 'Jazz', 'music'), ('rock', 'Rock', 'music'),
            ('classical', 'Classical', 'music'), ('electronic', 'Electronic', 'music'),
            ('hip-hop', 'Hip Hop', 'music'), ('comedy', 'Comedy', 'arts'),
            ('theatre', 'Theatre', 'arts'), ('film', 'Film', 'arts');
        INSERT INTO category (id, name, parent_id) VALUES ('bebop', 'Bebop', 'jazz');
        INSERT INTO category_alias (alias, category_id)
            SELECT id, id FROM category
            UNION SELECT lower(name), id FROM category;
        INSERT INTO category_alias (alias, category_id) VALUES
            ('science', 'science-and-tech'), ('technology', 'science-and-tech'),
            ('science and technology', 'science-and-tech'),
            ('food and drink', 'food-and-drink'), ('sports and fitness', 'sports-and-fitness'),
            ('business & professional', 'business'), ('community & culture', 'community'),
            ('performing & visual arts', 'arts'), ('theater', 'theatre');
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE category_alias;
        DROP TRIGGER category_cycle ON category;
        DROP FUNCTION check_category_cycle();
        DROP TABLE category;
    """)
