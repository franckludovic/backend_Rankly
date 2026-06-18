from sentence_transformers import SentenceTransformer, util
import os

print("Downloading all-MiniLM-L6-v2 (~80MB)...")
model = SentenceTransformer("all-MiniLM-L6-v2")

cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
print(f"\nModel cached at: {cache_dir}")

kw_emb = model.encode("buy laptops")
txt_emb = model.encode("laptops for sale cheap prices")
score = util.cos_sim(kw_emb, txt_emb).item()
print(f"Test similarity (buy laptops vs laptop store text): {score:.4f}")
print("Download complete ✓")
