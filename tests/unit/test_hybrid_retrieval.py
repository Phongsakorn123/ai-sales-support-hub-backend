import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_agent.schemas.product_suggestion import ParsedRequirements
from app.ai_agent.services.requirements import SearchCriteria
from app.ai_agent.services import embeddings
from app.ai_agent.services.retrieval import reciprocal_rank_fusion, retrieve_candidates
from app.backend.models.document import Document  # noqa: F401
from app.backend.models.product import Product, ProductSpecification
from app.backend.models.product_embedding import ProductEmbedding
from app.db.base import Base
from scripts.reindex_product_embeddings import reindex_products


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine)
        with self.Session() as db:
            db.add_all(
                [
                    Product(
                        name="Creative Laptop",
                        sku="CREATIVE",
                        category="Computer",
                        type="Laptop",
                        brand="Acme",
                        selling_price=Decimal("40000"),
                        currency="THB",
                        stock_quantity=4,
                        description="Suitable for video editing, digital artwork and creative applications.",
                        specification_rows=[
                            ProductSpecification(key="RAM", value="32GB", position=0)
                        ],
                    ),
                    Product(
                        name="Office Laptop",
                        sku="OFFICE",
                        category="Computer",
                        type="Laptop",
                        brand="Bolt",
                        selling_price=Decimal("42000"),
                        currency="THB",
                        stock_quantity=5,
                        description="Suitable for office productivity and spreadsheets.",
                        specification_rows=[
                            ProductSpecification(key="RAM", value="8GB", position=0)
                        ],
                    ),
                ]
            )
            db.commit()
            products = db.query(Product).order_by(Product.id).all()
            db.add_all(
                [
                    ProductEmbedding(
                        product_id=products[0].id,
                        model_name="test:normalized-v1",
                        content_hash="a",
                        vector=[1.0, 0.0],
                    ),
                    ProductEmbedding(
                        product_id=products[1].id,
                        model_name="test:normalized-v1",
                        content_hash="b",
                        vector=[0.0, 1.0],
                    ),
                ]
            )
            db.commit()
            self.product_ids = {product.sku: product.id for product in products}

        self.saved = (
            __import__(
                "app.core.config", fromlist=["settings"]
            ).settings.semantic_embedding_model,
            __import__(
                "app.core.config", fromlist=["settings"]
            ).settings.retrieval_rrf_k,
        )
        from app.core.config import settings

        settings.semantic_embedding_model = "test"
        settings.retrieval_rrf_k = 60

    def tearDown(self):
        from app.core.config import settings

        settings.semantic_embedding_model, settings.retrieval_rrf_k = self.saved
        self.engine.dispose()
        self.temp.cleanup()

    def criteria(self, query, keywords):
        return SearchCriteria(
            parsed=ParsedRequirements(), keywords=keywords, query_text=query
        )

    def test_semantic_paraphrase_finds_product_keyword_search_misses(self):
        with self.Session() as db, mock.patch.object(
            embeddings, "embed_text", return_value=[1.0, 0.0]
        ):
            result = retrieve_candidates(
                db,
                self.criteria("graphic design", ["graphic", "design"]),
                include_out_of_stock=False,
                max_products=3,
            )
        self.assertEqual(result.retrieval_mode, "hybrid")
        self.assertEqual(result.candidates[0].product.sku, "CREATIVE")
        self.assertIsNone(result.candidates[0].keyword_rank)
        self.assertEqual(result.candidates[0].semantic_rank, 1)

    def test_thai_query_uses_semantic_candidates(self):
        with self.Session() as db, mock.patch.object(
            embeddings, "embed_text", return_value=[1.0, 0.0]
        ):
            result = retrieve_candidates(
                db,
                self.criteria("โน้ตบุ๊กสำหรับออกแบบกราฟิก", ["โน้ตบุ๊ก", "ออกแบบกราฟิก"]),
                include_out_of_stock=False,
                max_products=3,
            )
        self.assertEqual(result.candidates[0].product.sku, "CREATIVE")

    def test_rrf_deduplicates_and_rewards_products_found_by_both_methods(self):
        fused = reciprocal_rank_fusion(
            [10, 20],
            [(20, 0.3), (30, 0.2)],
            k=60,
            keyword_weight=1.0,
            semantic_weight=1.0,
        )
        self.assertEqual(len(fused), 3)
        self.assertGreater(fused[20]["rrf_score"], fused[10]["rrf_score"])
        self.assertEqual(fused[20]["keyword_rank"], 2)
        self.assertEqual(fused[20]["semantic_rank"], 1)

    def test_missing_vectors_report_keyword_fallback(self):
        with self.Session() as db, mock.patch.object(
            embeddings, "semantic_search", return_value=([], 0)
        ):
            result = retrieve_candidates(
                db,
                self.criteria("creative laptop", ["creative", "laptop"]),
                include_out_of_stock=False,
                max_products=3,
            )
        self.assertEqual(result.retrieval_mode, "keyword_fallback")
        self.assertTrue(any("No product embeddings" in note for note in result.notes))

    def test_vector_failure_falls_back_without_failing_search(self):
        with self.Session() as db, mock.patch.object(
            embeddings, "semantic_search", side_effect=RuntimeError("offline")
        ):
            result = retrieve_candidates(
                db,
                self.criteria("creative laptop", ["creative", "laptop"]),
                include_out_of_stock=False,
                max_products=3,
            )
        self.assertEqual(result.retrieval_mode, "keyword_fallback")
        self.assertTrue(
            any("Semantic search is unavailable" in note for note in result.notes)
        )

    def test_reindex_command_handles_existing_products_and_dry_run(self):
        with self.Session() as db:
            products = db.query(Product).all()
            with mock.patch(
                "scripts.reindex_product_embeddings.try_sync_product_embedding",
                return_value=True,
            ) as sync:
                self.assertEqual(
                    reindex_products(db, products, dry_run=True), (0, 0, 2)
                )
                sync.assert_not_called()
                self.assertEqual(reindex_products(db, products), (2, 0, 2))
                self.assertEqual(sync.call_count, 2)

    def test_constraints_are_checked_after_semantic_retrieval(self):
        criteria = self.criteria("graphic design", ["graphic", "design"])
        criteria.parsed.budget_max = Decimal("30000")
        criteria.parsed.currency = "THB"
        criteria.brand_filter = "Acme"
        criteria.min_ram_gb = 64
        with self.Session() as db, mock.patch.object(
            embeddings, "embed_text", return_value=[1.0, 0.0]
        ):
            result = retrieve_candidates(
                db, criteria, include_out_of_stock=False, max_products=3
            )
        creative = next(
            candidate
            for candidate in result.candidates
            if candidate.product.sku == "CREATIVE"
        )
        self.assertIn("Preferred brand: Acme", creative.satisfied)
        self.assertTrue(any("Over budget" in value for value in creative.unmet))
        self.assertTrue(any("32GB is below" in value for value in creative.unmet))


if __name__ == "__main__":
    unittest.main()
