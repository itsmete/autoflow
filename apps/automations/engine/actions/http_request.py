from .base import BaseAction
from ..exceptions import ExecutionError
import requests


class HttpRequestAction(BaseAction):
        """
        A generic HTTP call whose response becomes the step's output.

        The point is not the call - `webhook` already makes one - but that the answer
        comes back. A run can call an internal API, read `{{steps.check.output.json.count}}`
        in the next step and branch on it, which is the whole reason ROADMAP section
        4.3 lists this action in Faz 1.

        `requires_channel` is False: the credentials, if any, are headers in the step
        config rather than a Channel row.
        """

        provider_class = None
        requires_channel = False

        def _execute_without_channel(self,prepared_data):
                url = prepared_data.get('url')

                if not url:
                        raise ExecutionError("HTTP request requires 'url'")

                method = str(prepared_data.get('method') or 'GET').upper()
                headers = prepared_data.get('headers') or {}
                # None, not {}: an empty dict would append a bare "?" to the url.
                params = prepared_data.get('query_params') or None
                body = prepared_data.get('body')
                timeout = prepared_data.get('timeout') or 10

                try:
                        resp = requests.request(
                                method = method,
                                url = url,
                                headers = headers,
                                params = params,
                                json = body if method in ('POST','PUT','PATCH','DELETE') and body else None,
                                timeout = timeout,
                        )
                except requests.RequestException as e:
                        raise ExecutionError(str(e)) from e

                # 4xx/5xx are not "the call worked but returned something sad": the
                # caller asked for an effect and it did not happen. A silent success
                # would let the next step act on a body that is an error page.
                if resp.status_code >= 400:
                        raise ExecutionError(
                                f"HTTP {resp.status_code}: {resp.text[:200]}"
                        )

                try:
                        json_data = resp.json()
                except ValueError:
                        json_data = None

                # A dict, because BaseAction keeps dict results as the step's output
                # (`status_code`, `json`, `text`, `headers` become addressable
                # expressions in the next step).
                return {
                        'sent' : True,
                        'status_code' : resp.status_code,
                        'json' : json_data,
                        'text' : getattr(resp,'text',''),
                        'headers' : dict(getattr(resp,'headers',{}) or {}),
                }
