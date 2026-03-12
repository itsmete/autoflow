from .base import * 

DEBUG= True

ALLOWED_HOSTS = ['*'] # ALLOWED ALL HOSTS 

DATABASES = {
        'default' : {
                'ENGINE' : 'django.db.backends.sqlite3',
                'NAME' : BASE_DIR / 'db.sqlite3',
        }
}

CORS_ALLOW_ALL_ORIGINS = True # accept requests from all origins in dev




#Write down SQL queries in dev mode
LOGGING = {
        'version' : 1,
        'handlers' : {
                'console' : {'class': 'logging.StreamHandler'}

        },
        'loggers' : {
                'django.db.backends' : {
                        'handlers' : ['console'],
                        'level' :'DEBUG', #show all sql queries
                }
        }
}

