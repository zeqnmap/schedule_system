"""Algorithm rule CRUD endpoints."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import database, models, schemas
from ..security import require_admin

router = APIRouter()


@router.get("/algorithm-rules/", response_model=List[schemas.AlgorithmRuleOut])
def read_algorithm_rules(_: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    return db.query(models.AlgorithmRule).order_by(models.AlgorithmRule.subject_name, models.AlgorithmRule.course).all()


@router.post("/algorithm-rules/", response_model=schemas.AlgorithmRuleOut)
def create_algorithm_rule(data: schemas.AlgorithmRuleCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    if not data.subject_name.strip() or data.weekly_hours < 1:
        raise HTTPException(status_code=400, detail="Укажите предмет и минимум 1 час в неделю")
    if data.group_id is None and data.course is None:
        raise HTTPException(status_code=400, detail="Выберите курс или конкретную группу")
    if data.lesson_mode not in {"auto", "lessons", "pairs", "pair_and_lesson"}:
        raise HTTPException(status_code=400, detail="Неизвестный режим занятий")
    if data.term_number not in (None, 1, 2):
        raise HTTPException(status_code=400, detail="Можно выбрать только 1-й или 2-й семестр")
    rule = models.AlgorithmRule(**data.model_dump(exclude={"subject_name"}), subject_name=data.subject_name.strip())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/algorithm-rules/{rule_id}")
def delete_algorithm_rule(rule_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    rule = db.query(models.AlgorithmRule).filter_by(id=rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    db.delete(rule)
    db.commit()
    return {"ok": True}
