from .celery import app as celery_app

# when django is started, celery will too.

__all__ = ['celery_app']