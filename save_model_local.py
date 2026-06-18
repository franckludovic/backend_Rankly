from sentence_transformers import SentenceTransformer
import os

model = SentenceTransformer("all-MiniLM-L6-v2")
save_path = "./models/semantic_model"

os.makedirs(save_path, exist_ok=True)
model.save(save_path)
print(f"Model saved to {save_path}")
print(f"Files: {os.listdir(save_path)}")
