"""
Automation Architecture

Trigger -> Condition -> Action
 
The engine should be consist of 
        - Condition Evaluator
        - Action Executor
        - General Orchestrator 

Each action must have its specific file in actions/ folder, (e.g. actions/send_email.py , actions/change_db_obj.py )





Action executor - A general orchestrator for managing actions.
        It shall run the actions via registry (action codes will be into actions/ folder, seperately)
        It shall apply on-failure policy (stop,retry, continue)
        It shall log
        
        
        That is, action executor is a runner loop


Where the data is coming from (to the evalutaor)

In scheduled trigger , there is initial value,
For webhooks, payload will be used. 

Evaluator should take data in one interface for both scenarios.
        


"""