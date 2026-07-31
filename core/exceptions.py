from rest_framework.views import exception_handler,status
from rest_framework.response import Response
from django.utils.translation import gettext_lazy as _
import logging

logger = logging.getLogger(__name__)

ERROR_CODES = {
    400: 'validation_error',
    401: 'unauthorized',
    403: 'permission_denied',
    404: 'not_found',
    500: 'server_error',
}

def custom_exception_handler(exc, context):
        # Önce DRF'nin kendi handler'ını çalıştır
        response = exception_handler(exc, context)

        # DRF handle edemediyse (500 hataları gibi)
        if response is None:
                return Response({
                'error': 'Beklenmeyen bir hata oluştu.',
                'detail': str(exc)
                }, status=500)

        # DRF'nin response'unu standart formata sar
        return Response({
                'error': response.data,
        }, status=response.status_code)



def extended_exception_handler(exc,context):

        response = exception_handler(exc,context)

        if response is None:
                # DRF could not map this to an API error, so it is a real bug.
                # Without this the traceback is lost and every defect looks like an
                # opaque 500 - which is exactly how a TypeError in core/roles.py
                # survived unnoticed in every /api/v1/users/ endpoint.
                view = context.get('view') if context else None
                request = context.get('request') if context else None
                logger.exception(
                        "Unhandled exception in %s (%s %s)",
                        view.__class__.__name__ if view else 'unknown view',
                        getattr(request,'method','?'),
                        getattr(request,'path','?'),
                        exc_info=exc,
                )

                return Response({

                        "success" : False,
                        "error" : {
                                "code" : "500" ,
                                "message" : _("An unexpexted error occured"),
                                "fields" : {}
                        }
                },status=status.HTTP_500_INTERNAL_SERVER_ERROR)


        data = response.data

        #message based exceptions
        if 'detail' in data:
                message = str(data['detail'])
                fields = {}
        # non-fields , generally validation, errors
        elif 'non_field_errors' in data:
                message = str(data['non_field_errors'][0])
                fields = {}
        #validation error
        else : 
                message = "Validation Error"
                fields = data

        return Response({
                "success" : False,
                "error" : {
                        "code" : ERROR_CODES.get(response.status_code,'unknown_error'),
                        "message" : message,
                        "fields" : fields

                }
        },status=response.status_code)