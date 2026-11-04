
from ..base import BaseConfigSerializer

from django.utils.translation import gettext_lazy as _
from ..validators import url_validator



class HttpRequestActionSerializer(BaseConfigSerializer):
        """
        The generic integration node (ROADMAP section 4.3).

        Same shape as the webhook serializer, but every field the request needs
        except the url is optional: `method` defaults to GET and the rest default to
        empty, so "call this API" does not require the client to spell out six
        fields. `timeout` is exposed because an integration that hangs must not
        hang the run with it.
        """

        CONFIG_SCHEMA = {
                "url" : {
                        "type" : "string",
                        "required" : True,
                        "validator" : url_validator
                },
                "method" : {
                        "type" : "string",
                        "required" : False,
                        "default" : 'GET',
                        "choices" : ['GET','POST','PUT','PATCH','DELETE']
                },
                "headers" : {
                        "type" : "dict",
                        "required" : False,
                        "default" : {}
                },
                "query_params" : {
                        "type" : "dict",
                        "required" : False,
                        "default" : {}
                },
                "body" : {
                        "type" : "dict",
                        "required" : False,
                        "default" : {}
                },
                "timeout" : {
                        "type" : "integer",
                        "required" : False,
                        "default" : 10
                },
        }
