# SEO Suggestion Engine- Backend API

## Structure
```
backend/
  main.py                  ← FastAPI entry point
  config.py                ← all settings
  requirements.txt         ← dependencies
  deploy.sh                ← EC2 deployment script
  .env                     ← API keys (never commit this)
  models/
    model_registry.py      ← loads/swaps models
    xgb_classifier.joblib  ← copy from notebook
    xgb_regressor.joblib   ← copy from notebook
    label_encoder.joblib   ← copy from notebook
    clf_feature_cols.joblib← copy from notebook
    reg_feature_cols.joblib← copy from notebook
  services/
    feature_extractor.py   ← scrapes URL → features
    external_apis.py       ← Lighthouse + OPR (async/parallel)
    predictor.py           ← runs ML models
    recommender.py         ← generates recommendations
  api/
    routes.py              ← all endpoints
```

## Quick Start (local)
```bash
pip install -r requirements.txt
cp your_models/*.joblib models/
uvicorn main:app --reload --port 8000
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /analyse | Scrape URL + full analysis |
| POST | /analyse/extension | Extension features + analysis |
| POST | /analyse/file | HTML file upload |
| GET  | /health | Health check |
| GET  | /model/status | Model info |
| POST | /model/reload | Hot-swap models |

## Swapping Models (when you get better ones)
```bash
# 1. Copy new model files to models/ folder
cp new_xgb_classifier.joblib models/xgb_classifier.joblib

# 2. Update accuracy in config (optional)
# edit models/model_registry.py- change clf_accuracy value

# 3. Hot-reload without restart
curl -X POST http://localhost:8000/model/reload
```

## EC2 Deployment
```bash
chmod +x deploy.sh
./deploy.sh
```

## Example Request
```bash
curl -X POST http://localhost:8000/analyse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "keyword": "travel tips"}'
```

## Example Response
```json
{
  "url": "https://example.com",
  "keyword": "travel tips",
  "analysis_mode": "full",
  "quality": "High",
  "confidence": 87.3,
  "predicted_rank": 8,
  "rank_tier": "top 10",
  "lighthouse_score": 94,
  "recommendations": [
    {
      "priority": 3,
      "impact": "MEDIUM",
      "category": "Images",
      "issue": "2 images missing alt text",
      "action": "Add descriptive alt text to all 5 images",
      "metric": "3/5 images have alt text"
    }
  ]
}
```
