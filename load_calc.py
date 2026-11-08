def calculate_weights(profile: dict, exercises: list, week_index: int = 0) -> list:
    """
    Рассчитывает веса под профиль пользователя.
    exercise: {"name": "Жим гантелей", "type": "dumbbell", "base_ratio": 0.3}
    """
    bw = profile.get("weight", 60) # масса тела
    sex = profile.get("sex", "male")
    goal = profile.get("goal", "maintain")
    
    # Коэффициенты
    sex_mult = 1.0 if sex == "male" else 0.75
    goal_mult = 1.0
    if goal == "cut": goal_mult = 0.9
    if goal == "bulk": goal_mult = 1.1
    
    calculated = []
    
    for ex in exercises:
        ex_copy = ex.copy()
        ex_type = ex.get("type", "bodyweight")
        
        if ex_type == "bodyweight":
            ex_copy["weight_kg"] = 0
            ex_copy["reps"] = 10 + (week_index * 2)
            ex_copy["note"] = "Собственный вес"
            calculated.append(ex_copy)
            continue
            
        ratio = ex.get("base_ratio", 0.4)
        
        # Базовый вес = масса тела * коэффициент упражнения * пол * цель
        base_weight = bw * ratio * sex_mult * goal_mult
        
        # Прогрессия: +2.5% за каждую неделю
        base_weight *= (1 + 0.025 * week_index)
        
        # Округление (штанга - шаг 2.5кг, гантели - 2кг)
        step = 2.5 if ex_type == "barbell" else 2.0
        final_weight = max(step, round(base_weight / step) * step)
        
        ex_copy["weight_kg"] = final_weight
        ex_copy["reps"] = 8 + week_index
        ex_copy["note"] = f"~{int(ratio*100)}% от массы тела"
        calculated.append(ex_copy)
        
    return calculated
