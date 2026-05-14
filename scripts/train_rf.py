import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.datasets import make_classification

# Generate dummy data
X, y = make_classification(n_samples=1000, n_features=20, random_state=42)

# Simple Model
model = RandomForestClassifier(n_estimators=100)
model.fit(X, y)

print(f"Model trained successfully! Accuracy: {model.score(X, y):.2f}")