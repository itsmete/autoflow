
"""
Will be called by orchestrator by A bunch of AutomationCondition object, 

"""
from ..models import AutomationCondition,ConditionOperators,LogicalOperator
from .exceptions import EvaluationError
EVALUATOR_MAP = {
        # d -> data, v -> value
        ConditionOperators.EQUALS : lambda d,v : d == v,
        ConditionOperators.NOT_EQUALS : lambda d,v : d != v,

        ConditionOperators.CONTAINS : lambda d,v :  v in d ,
        ConditionOperators.IN : lambda d,v : d in v.split(","),
        
        ConditionOperators.GREATER : lambda  d,v : int(d) > int(v),
        ConditionOperators.LESS : lambda d,v : int(d) < int(v),
        
}

class ConditionEvaluator:

        def _evaluate_single(self,condition : AutomationCondition,data) -> bool:
                
                evaluator_func = EVALUATOR_MAP.get(condition.operator)
                if not evaluator_func:
                        raise EvaluationError("Evaluator function couldn't be called")
        
                field = condition.field

                # field_val = data[field]
                field_val = data.get(field) # .get() method returns None if key couldn't be found, but data[field] throws KeyError
                
                if field_val is None:
                        raise EvaluationError(f"Field value ({field}) is not found in data ")

                        
                value = condition.value
                try:
                        return evaluator_func(field_val,value)
                except ValueError:
                        raise EvaluationError(f"Type conversion failed for field '{field}' : cannot compare '{field_val}' with '{value}")



        def evaluate_conditions(self,conditions : AutomationCondition,data) -> bool:
                # first condition's logical operator is ignored
                # conditions are evaulated in order, side by side

                if not conditions:
                        return True
                
                result = self._evaluate_single(conditions[0],data)
                
                for c in conditions[1:]:
                        current_result = self._evaluate_single(c, data)

                        if c.logical_operator == LogicalOperator.AND:
                                result = result and current_result
                        elif c.logical_operator == LogicalOperator.OR:
                                result = result or current_result
                        else:
                                raise EvaluationError(f"Invalid logical operator for condition {c}")
                        
                return result


                