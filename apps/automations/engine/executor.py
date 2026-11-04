"""
Execution: the legacy action list, and the step graph that replaces it.

Two entry points live here on purpose, for one release:

  * `execute_actions()` runs an ordered `AutomationAction` list. It is what every
    existing automation does today and its behaviour is frozen - the actions API
    and all of its tests depend on it (ROADMAP section 5.1, "backward compatibility
    contract").

  * `execute_graph()` walks `AutomationStep` + `AutomationEdge`, captures each
    step's output into `context['steps'][key]`, and understands the control-flow
    steps a flat list cannot express: `filter`, `branch`, `wait`, `set_variable`.

Nothing here imports Django. Steps arrive as whatever has the right attributes -
an ORM row, or a SimpleNamespace in a test - which is what keeps the engine
shareable and what tests/test_engine_purity.py measures.
"""
import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from .actions import get_action_class
from .evaluator import ConditionEvaluator
from .exceptions import ExecutionError
from .expressions import RESERVED_NAMESPACES, resolve_value


# The step types this executor knows how to run. Strings rather than the TextChoices
# class on purpose: importing it would mean importing the Django app's models, and
# the engine must be able to run without them. The values are the contract with
# models.StepTypes, and a data migration is what keeps them equal.
STEP_ACTION = 'action'
STEP_FILTER = 'filter'
STEP_BRANCH = 'branch'
STEP_WAIT = 'wait'
STEP_SET_VARIABLE = 'set_variable'

# Same story for the failure policies and the result statuses: engine vocabulary,
# not ORM vocabulary.
ON_FAILURE_STOP = 'stop'
ON_FAILURE_CONTINUE = 'continue'
ON_FAILURE_RETRY = 'retry'

RESULT_SUCCESS = 'success'
RESULT_SKIPPED = 'skipped'
RESULT_WAITING = 'waiting'
RESULT_FAILED = 'failed'

# `24h`, `15m`, `30s`, `7d`. A bare number reads as seconds.
_WAIT_UNITS = {'s' : 1,'m' : 60,'h' : 3600,'d' : 86400}


@dataclass
class GraphExecutionResult:
        """
        What one pass over the graph did.

        `status='waiting'` is not a failure: the run did everything it could and
        handed the rest to a scheduled continuation, which re-enters the graph at
        `resume_from_step_key` with this same context.
        """
        status: str
        context: dict
        step_results: list
        waiting_until: str | None = None
        resume_from_step_key: str | None = None


def _as_utc(moment):
        """Naive datetimes are read as UTC; comparing aware to naive raises."""
        if moment.tzinfo is None:
                return moment.replace(tzinfo = timezone.utc)
        return moment


def _duration_seconds(value) -> int:
        """
        `"24h"` / `30` / `"30s"` -> seconds.

        Raises rather than guessing: a wait step that silently waits zero seconds,
        or a day instead of a minute, is a scheduling bug nobody notices until a
        customer does.
        """
        if isinstance(value,bool):
                raise ExecutionError("Unusable wait duration: %r" % (value,))

        if isinstance(value,(int,float)):
                if value < 0:
                        raise ExecutionError("A wait duration cannot be negative: %r" % (value,))
                return int(value)

        if isinstance(value,str):
                text = value.strip().lower()

                if text:
                        try:
                                return _duration_seconds(int(text))
                        except ValueError:
                                pass

                        unit = text[-1]
                        if unit in _WAIT_UNITS:
                                try:
                                        amount = float(text[:-1])
                                except ValueError:
                                        amount = None

                                if amount is not None and amount >= 0:
                                        return int(amount * _WAIT_UNITS[unit])

        raise ExecutionError("Unusable wait duration: %r" % (value,))


def _parse_iso_datetime(value) -> datetime:
        if isinstance(value,datetime):
                return _as_utc(value)

        if isinstance(value,date):
                return _as_utc(datetime(value.year,value.month,value.day))

        if isinstance(value,str):
                text = value.strip()

                # `2026-08-03T12:00:00Z` is the most common ISO spelling and
                # fromisoformat() before 3.11 refuses the military suffix.
                if text.endswith('Z'):
                        text = text[:-1] + '+00:00'

                try:
                        return _as_utc(datetime.fromisoformat(text))
                except ValueError:
                        pass

        raise ExecutionError("Unusable wait time: %r" % (value,))


def parse_wait_duration(config,context,now = None):
        """
        A `wait` step's config -> (delay_seconds, eta).

        Two spellings, one answer. `duration` is relative ("24h") and `until` is
        absolute, resolved through the expression layer so
        `{{trigger.appointment_time}}` works. Callers pass `now` in tests so the
        arithmetic is not a race against the wall clock.
        """
        config = config if isinstance(config,Mapping) else {}
        context = context if isinstance(context,Mapping) else {}

        reference = _as_utc(now) if isinstance(now,datetime) else datetime.now(timezone.utc)

        duration = resolve_value(config['duration'],context) if 'duration' in config else None
        until = resolve_value(config['until'],context) if 'until' in config else None

        if duration is not None and duration != '':
                delay_seconds = _duration_seconds(duration)
                return delay_seconds, reference + timedelta(seconds = delay_seconds)

        if until is not None and until != '':
                eta = _parse_iso_datetime(until)
                delay_seconds = max(0,int(math.ceil((eta - reference).total_seconds())))
                return delay_seconds, eta

        raise ExecutionError("A wait step needs either 'duration' or 'until'")


def _condition_objects(conditions):
        """
        Turn plain config dicts into the attribute-accessible shape the evaluator
        reads, filling in the defaults the model would have filled in.

        `quantifier` is normalised to the engine's own empty-string sentinel for
        "one value": the evaluator treats a truthy quantifier as a request to fan
        out over a collection, so the word "single" there means the opposite of
        what it says.
        """
        objects = []

        for item in conditions or []:
                if isinstance(item,Mapping):
                        quantifier = item.get('quantifier') or ''
                        if quantifier == 'single':
                                quantifier = ''

                        objects.append(SimpleNamespace(
                                field = item.get('field'),
                                operator = item.get('operator','eq'),
                                value = item.get('value'),
                                quantifier = quantifier,
                                on_missing = item.get('on_missing','fail'),
                                logical_operator = item.get('logical_operator','and'),
                        ))
                else:
                        objects.append(item)

        return objects


def _step_key(step):
        key = getattr(step,'key',None)
        return str(key) if key is not None else str(getattr(step,'id',''))


def _branch_key(edge):
        # A synthetically chained edge (no edges in the automation at all) is None;
        # it matches no branch and belongs to the plain "" path.
        return getattr(edge,'branch_key','') or ''


def _edge_sort_key(item):
        """
        Edge order, then creation order.

        `created_at` is coerced because a saved row has a datetime and an unsaved
        one has None, and comparing datetime to 0 in the same tuple raises
        TypeError - which would only ever happen in the middle of a run.
        """
        edge = item[1]
        created = getattr(edge,'created_at',None)
        created_sort = created.timestamp() if isinstance(created,datetime) else 0
        return (getattr(edge,'order',0) or 0,created_sort)


class AutomationExecutor:

        MAX_RETRIES = 3

        def _execute_single(self,action,data):

                action_type = getattr(action,'action_type',None)
                action_cls = get_action_class(action_type)

                if action_cls is None:
                        # Silently doing nothing is how an automation with a typo'd
                        # action type would "succeed" without sending anything.
                        raise ExecutionError(f"Unknown action type: {action_type}")

                status = action_cls(action).run(data)

                return status


        def execute_actions(self,actions,data):
                """
                Run an ordered list of AutomationAction rows.

                Unchanged, and frozen for the whole of Faz 1: automate-the-actions
                clients and their tests rely on this exact contract.
                """
                if not actions:
                        raise ExecutionError("No action is found")

                
                for action in actions:
                        try:
                                self._execute_single(action,data)
                        except ExecutionError:
                                if getattr(action,'on_failure',None) == ON_FAILURE_RETRY:
                                        self._retry(action,data)

                                elif getattr(action,'on_failure',None) == ON_FAILURE_STOP:
                                        return False

                                elif getattr(action,'on_failure',None) == ON_FAILURE_CONTINUE:
                                        pass


                return True


        def _retry(self,action,data):
                """
                Re-run a failed action up to MAX_RETRIES times.

                The counter has to advance on EVERY attempt, not only on the ones that
                raise. The original loop only incremented inside `except`, so an action
                that returned falsy without raising spun forever and hung the worker.
                """
                for _attempt in range(self.MAX_RETRIES):
                        try:
                                if self._execute_single(action,data):
                                        return True
                        except ExecutionError:
                                continue

                raise ExecutionError(
                        f"An action ({getattr(action,'id','?')}) failed on all {self.MAX_RETRIES} attempts"
                )


        #
        # Graph execution (Faz 1.D)
        #

        def execute_graph(self,steps,edges,context,*,evaluator = None,
                          schedule_continuation = None,start_step_key = None):
                """
                Walk the step graph once and return a GraphExecutionResult.

                Synchronous and single threaded: a `wait` step does not sleep, it
                stops, records where it stopped and asks `schedule_continuation` to
                bring the run back later. Everything else is depth-agnostic - the
                queue is a worklist, so a fan-out and a merge need no special case.
                """
                steps = list(steps or [])
                edges = list(edges or [])

                evaluator = evaluator or ConditionEvaluator()

                # The caller's dict is updated in place: a resumed run is handed the
                # previous run's context, and copying it would throw away exactly the
                # `steps` and `vars` it came back for.
                if not isinstance(context,dict):
                        context = dict(context or {})

                if not isinstance(context.get('steps'),dict):
                        context['steps'] = {}
                if not isinstance(context.get('vars'),dict):
                        context['vars'] = {}

                steps_by_key = {}
                steps_by_id = {}

                for step in steps:
                        key = _step_key(step)
                        steps_by_key[key] = step
                        steps_by_id[str(getattr(step,'id',''))] = step

                outgoing_by_key,entry = self._index_edges(edges,steps_by_key,steps_by_id)

                # No edges at all: read the steps as the chain their creation order
                # already describes. An automation authored through the actions API
                # and not yet re-saved is exactly this shape.
                keys = list(steps_by_key)
                if not edges:
                        for previous,current in zip(keys,keys[1:]):
                                outgoing_by_key[previous] = [(current,None)]

                queue = deque(self._initial_queue(
                        keys,entry,steps_by_key,start_step_key,outgoing_by_key
                ))

                executed = set()
                step_results = []
                failed = False
                waiting = False
                waiting_until = None
                resume_from_step_key = None
                # A filter that said "no" ends a path, not the run. It only becomes
                # the run's status if nothing succeeded afterwards.
                filter_stopped = False

                while queue:
                        key = queue.popleft()

                        if key in executed:
                                continue

                        executed.add(key)

                        step = steps_by_key.get(key)
                        if step is None:
                                # A dangling edge target. Nothing to run, and the
                                # alternative - raising - would kill a run over a
                                # half-deleted node.
                                continue

                        step_type = getattr(step,'step_type',STEP_ACTION) or STEP_ACTION
                        outgoing = outgoing_by_key.get(key,[])

                        if step_type == STEP_ACTION:
                                try:
                                        output = self._execute_step_action(step,context)
                                except ExecutionError as error:
                                        context['steps'][key] = {'status' : 'failed','error' : str(error)}
                                        step_results.append(self._record(key,step_type,context))

                                        policy = getattr(step,'on_failure',ON_FAILURE_STOP) or ON_FAILURE_STOP

                                        if policy == ON_FAILURE_CONTINUE:
                                                # Same contract as execute_actions():
                                                # 'continue' means the rest of the
                                                # flow still gets its turn.
                                                self._enqueue(queue,executed,outgoing)
                                                continue

                                        if policy == ON_FAILURE_RETRY:
                                                # _execute_step_action() has already spent
                                                # its attempts. A step that cannot be
                                                # completed is an error of the run, not
                                                # a status of it.
                                                raise

                                        # 'stop' ends the run here, after recording why.
                                        failed = True
                                        break

                                if output.get('sent'):
                                        context['steps'][key] = {'status' : 'success','output' : output}
                                        filter_stopped = False
                                else:
                                        # The provider said it did not send. That is
                                        # a failure, not a success with a note.
                                        context['steps'][key] = {
                                                'status' : 'failed',
                                                'output' : output,
                                                'error' : 'Action reported that it was not sent',
                                        }

                                        policy = getattr(step,'on_failure',ON_FAILURE_STOP) or ON_FAILURE_STOP
                                        if policy == ON_FAILURE_CONTINUE:
                                                step_results.append(self._record(key,step_type,context))
                                                self._enqueue(queue,executed,outgoing)
                                                continue

                                        step_results.append(self._record(key,step_type,context))
                                        failed = True
                                        break

                                step_results.append(self._record(key,step_type,context))
                                self._enqueue(queue,executed,outgoing)

                        elif step_type == STEP_SET_VARIABLE:
                                resolved = self._resolve_variables(step,context)

                                context['steps'][key] = {'status' : 'success','output' : resolved}
                                step_results.append(self._record(key,step_type,context))

                                self._enqueue(queue,executed,outgoing)

                        elif step_type == STEP_FILTER:
                                conditions = _condition_objects(self._step_config(step).get('conditions'))
                                decision = evaluator.evaluate_conditions(conditions,context)
                                passed = bool(decision)

                                context['steps'][key] = {
                                        'status' : 'success' if passed else 'skipped',
                                        'output' : {'passed' : passed},
                                        'decision' : decision.as_dict(),
                                }
                                step_results.append(self._record(key,step_type,context))

                                if passed:
                                        self._enqueue(queue,executed,outgoing)
                                else:
                                        # Not enqueued: this path ends. The run is
                                        # 'skipped', not 'failed' - the condition
                                        # did its job.
                                        filter_stopped = True

                        elif step_type == STEP_BRANCH:
                                chosen,branch_decisions = self._choose_branch(
                                        step,context,evaluator,outgoing
                                )

                                context['steps'][key] = {
                                        'status' : 'success',
                                        'output' : {'branch' : chosen},
                                        'decision' : branch_decisions,
                                }
                                step_results.append(self._record(key,step_type,context))

                                # Only the chosen path continues; the other branches
                                # are not "skipped steps", they were never reached.
                                for to_key,edge in outgoing:
                                        if chosen is not None and _branch_key(edge) == chosen:
                                                if to_key not in executed:
                                                        queue.append(to_key)

                        elif step_type == STEP_WAIT:
                                delay_seconds,eta = parse_wait_duration(self._step_config(step),context)

                                next_keys = [to_key for to_key,_edge in outgoing]
                                next_key = next_keys[0] if next_keys else None

                                pending_until = eta.isoformat() if eta else None

                                context['steps'][key] = {
                                        'status' : 'waiting',
                                        'output' : {
                                                'delay_seconds' : delay_seconds,
                                                'until' : pending_until,
                                                'resume_from' : next_key,
                                        },
                                }
                                step_results.append(self._record(key,step_type,context))

                                waiting = True
                                waiting_until = pending_until
                                resume_from_step_key = next_key

                                if next_key and schedule_continuation:
                                        schedule_continuation(
                                                next_key,context,
                                                delay_seconds = delay_seconds,
                                                eta = eta,
                                        )

                                # Deliberately not enqueued: the rest of the graph
                                # runs when the continuation fires, not now.

                        else:
                                # Step types Faz 1 does not execute yet (loop, ai_*,
                                # mcp_call, ...). Halting is the honest answer: the
                                # graph has a node whose effect nobody applied.
                                raise ExecutionError(f"Unsupported step type: {step_type}")

                if failed:
                        status = RESULT_FAILED
                elif waiting:
                        status = RESULT_WAITING
                elif filter_stopped:
                        status = RESULT_SKIPPED
                else:
                        status = RESULT_SUCCESS

                return GraphExecutionResult(
                        status = status,
                        context = context,
                        step_results = step_results,
                        waiting_until = waiting_until,
                        resume_from_step_key = resume_from_step_key,
                )


        def _index_edges(self,edges,steps_by_key,steps_by_id):
                """
                Resolve every edge to `(from_key, to_key)` pairs, once.

                Edges may carry step objects, step ids or step keys depending on who
                built them - the ORM gives objects, a serializer-in-progress gives
                keys, and a client payload gives ids. Resolving here means the walk
                below never has to care.
                """
                outgoing_by_key = {key : [] for key in steps_by_key}
                entry = []

                for edge in edges:
                        from_key = self._endpoint_key(getattr(edge,'from_step',None),
                                                      getattr(edge,'from_step_id',None),
                                                      steps_by_id)
                        to_key = self._endpoint_key(getattr(edge,'to_step',None),
                                                    getattr(edge,'to_step_id',None),
                                                    steps_by_id)

                        if to_key is None:
                                continue

                        if from_key is None:
                                entry.append((to_key,edge))
                        else:
                                outgoing_by_key.setdefault(from_key,[]).append((to_key,edge))

                for key in outgoing_by_key:
                        outgoing_by_key[key].sort(key = _edge_sort_key)

                entry.sort(key = _edge_sort_key)

                return outgoing_by_key,entry


        def _endpoint_key(self,endpoint,endpoint_id,steps_by_id):
                if endpoint is not None:
                        # A step object: its `key` is the name expressions use.
                        key = getattr(endpoint,'key',None)
                        return str(key) if key is not None else str(getattr(endpoint,'id',''))

                if endpoint_id:
                        step = steps_by_id.get(str(endpoint_id))
                        if step is not None:
                                return _step_key(step)

                return None


        def _initial_queue(self,keys,entry,steps_by_key,start_step_key,outgoing_by_key):
                if start_step_key is not None:
                        key = str(start_step_key)

                        if key not in steps_by_key:
                                raise ExecutionError(f"Unknown start step: {start_step_key}")

                        return [key]

                if entry:
                        return [to_key for to_key,_edge in entry]

                if not keys:
                        return []

                # No entry edge. Rather than run every step as if it were an entry
                # point - which would execute a branch's targets unconditionally -
                # start at the steps nothing points at. A graph whose entry edge was
                # lost still runs; it just runs from its head.
                pointed_at = {
                        to_key
                        for pairs in outgoing_by_key.values()
                        for to_key,_edge in pairs
                }

                heads = [key for key in keys if key not in pointed_at]

                return heads or [keys[0]]


        def _enqueue(self,queue,executed,outgoing):
                for to_key,_edge in outgoing:
                        if to_key not in executed:
                                queue.append(to_key)


        def _step_config(self,step):
                """A step's config as a dict, whether it is an ORM row or a stub."""
                config = getattr(step,'config',None)
                return config if isinstance(config,Mapping) else {}


        def _record(self,key,step_type,context):
                entry = context['steps'].get(key) or {}

                return {
                        'key' : key,
                        'step_type' : step_type,
                        'status' : entry.get('status'),
                        'output' : entry.get('output'),
                }


        def _execute_step_action(self,step,context):
                """
                Run one action step and return its output dict.

                `on_failure='retry'` is applied here because it is the one policy that
                changes how many times the action runs. 'stop' and 'continue' decide
                what the walk does next, so they stay in the walk.
                """
                action_type = getattr(step,'action_type',None)
                action_cls = get_action_class(action_type)

                if action_cls is None:
                        raise ExecutionError(f"Unknown action type: {action_type}")

                action = action_cls(step)
                policy = getattr(step,'on_failure',ON_FAILURE_STOP) or ON_FAILURE_STOP

                if policy != ON_FAILURE_RETRY:
                        return action.run_with_output(context)

                last_error = None

                for _attempt in range(self.MAX_RETRIES):
                        try:
                                output = action.run_with_output(context)
                        except ExecutionError as error:
                                last_error = error
                                continue

                        if output.get('sent'):
                                return output

                        last_error = ExecutionError(
                                "An action (%s) failed on all %s attempts"
                                % (str(getattr(step,'id','?')),self.MAX_RETRIES)
                        )

                raise last_error or ExecutionError(
                        "An action (%s) failed on all %s attempts"
                        % (str(getattr(step,'id','?')),self.MAX_RETRIES)
                )


        def _resolve_variables(self,step,context):
                """
                `set_variable`: resolve each declared variable against the run so far.

                One at a time, in config order, so a later variable can read an
                earlier one (`{"greeting": "...", "line": "{{vars.greeting}}!"}`).
                """
                variables = self._step_config(step).get('variables') or {}
                resolved = {}

                for name,value in variables.items():
                        resolved[name] = resolve_value(value,context)

                        context['vars'][name] = resolved[name]

                        # Also reachable without the `vars.` prefix, except for the
                        # names the context already owns - `vars.trigger = x` must
                        # not shadow the trigger namespace every expression uses.
                        if name not in RESERVED_NAMESPACES:
                                context[name] = resolved[name]

                return resolved


        def _choose_branch(self,step,context,evaluator,outgoing):
                """
                Pick one branch key and record why.

                Two shapes are supported: a `branches` list (each with a `key` and its
                own conditions, first match wins) and a single `conditions` block that
                yields 'true'/'false'. The fallback is, in order: config['default'],
                an edge named 'default', an edge named 'false'.
                """
                config = self._step_config(step)
                decisions = []
                chosen = None

                branches = config.get('branches') or []

                if branches:
                        for branch in branches:
                                conditions = _condition_objects(branch.get('conditions'))
                                decision = evaluator.evaluate_conditions(conditions,context)
                                branch_key = branch.get('key',branch.get('branch_key'))

                                decisions.append({
                                        'key' : branch_key,
                                        'passed' : bool(decision),
                                        'decision' : decision.as_dict(),
                                })

                                if chosen is None and bool(decision):
                                        chosen = branch_key
                elif 'conditions' in config:
                        conditions = _condition_objects(config.get('conditions'))
                        decision = evaluator.evaluate_conditions(conditions,context)
                        chosen = 'true' if decision else 'false'

                        decisions.append({
                                'key' : chosen,
                                'passed' : bool(decision),
                                'decision' : decision.as_dict(),
                        })

                if chosen is not None:
                        return chosen,decisions

                if config.get('default') is not None:
                        return config.get('default'),decisions

                names = {_branch_key(edge) for _to_key,edge in outgoing}

                if 'default' in names:
                        return 'default',decisions

                if 'false' in names:
                        return 'false',decisions

                return None,decisions
