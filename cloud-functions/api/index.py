"""Makers 入口 —— 最小验证版(不依赖backend)"""
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "msg": "supplykit-edgeone minimal entry"}


@app.get("/")
def root():
    return {"ok": True}
