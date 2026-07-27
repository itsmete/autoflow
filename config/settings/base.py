from pathlib import Path
import environ

from core.__settings import SECRET_KEY


BASE_DIR = Path(__file__).resolve().parent.parent.parent 
# Points out the root directory, same level with maange.py
# Why we doubled .parent is that path(file).resolve() points out the file first
# And one .parent points out the directory where the file is located (config/settins), second is config/ , third is the root 




env = environ.Env()


environ.Env.read_env(BASE_DIR / '.env')


SECRET_KEY = env('SECRET_KEY')

DJANGO_APPS = [
        'django.contrib.admin',
        'django.contrib.auth',
        'django.contrib.contenttypes', # generic foereign key infras
        'django.contrib.sessions',
        'django.contrib.messages',
        'django.contrib.staticfiles',
]



THIRD_PARTY_APPS = [
        'rest_framework',
        'rest_framework_simplejwt', #  Auth
        'corsheaders' ,# to prevent CORS error if frontend is located in different domain
        'django_filters' , # "?status=active"
        'drf_spectacular', # automatic , sdagger / OpenAPI
        'django_celery_beat', # timed tasks are managed here, 
        'django_celery_results' # keeps task result in DB
]


#Modules that we wrote
LOCAL_APPS = [
        'core.apps.CoreConfig',
        'apps.tenants',
        'apps.channels',
        'apps.automations',
        'apps.websites',
        'apps.analytics',
        'apps.users',

]

ROOT_URLCONF = 'config.urls' # where django should look for urls 


STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'


WSGI_APPLICATION = 'config.wsgi.application'


INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS # django waits for only one list , so we merged


MIDDLEWARE = [
        'corsheaders.middleware.CorsMiddleware',
        # the order of middlewares is crucial and CORS middleware must be on top , because it welcomes the requests

        'django.middleware.security.SecurityMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


AUTH_USER_MODEL = 'users.User' # We are gonna define our User model

DATABASES = {
        'default' : {
                'ENGINE' : 'django.db.backends.postgresql',
                'NAME' : env('DB_NAME',default='autoflow-db'),
                'USER' : env('DB_USER',default='postgres'),
                'PASSWORD' : env('DB_PASSWORD',default=''),
                'HOST' : env('DB_HOST', default='localhost'),
                'POST' : env('DB_PORT', parse_default='5432'),
                'OPTIONS' : {
                        # If db connection is down, reverse transaction
                        'connect_timeout' : 10,
                },

                'CONN_MAX_AGE' : 60,
                # All DB processes in same request use the same connection
                # Keeps connection data instead of opening connection each time , gains performance
        }
}


SCRIPTS_DIR = BASE_DIR / 'core' / 'cache' / 'scripts'

REST_FRAMEWORK = {

        'DEFAULT_AUTHENTICATION_CLASSES' : [
                # Verify user identity by JWT Tokens
                # reads from "Authorization : Bearer <token>" 
                'rest_framework_simplejwt.authentication.JWTAuthentication',
        ],
        'DEFAULT_PERMISSON_CLASSES' : [
                # Any user is not signed in cannot reach any endpoint defaultly
                # Wİll be overridded for Public endpoints 
                'rest_framework.permissions.IsAuthenticated',
        ],
        'DEFAULT_FILTER_BACKENDS' : [
                
                # filtering by "?field=value" in URL
                'django_filters.rest_framework.DjangoFilterBackend',
                # ?search=word, 
                'rest_framework.filters.SearchFilter',
                # ?ordering=-created_at
                'rest_framework.filters.OrderingFilter'
        ],

        # all list endpoints are paged automatically
        'DEFAULT_PAGINATION_CLASS' : 'core.pagination.StandartPagination',
        'PAGE_SIZE' : 20,

        #drf-spectatular swagger documentation
        'DEFAULT_SCHEMA_CLASS' : 'drf_spectacular.openapi.AutoSchema',

        # consistent format for error handling
        'EXCEPTION_HANDLER' : 'core.exceptions.extended_exception_handler',

}



from datetime import timedelta

SIMPLE_JWT = {

        'ACCESS_TOKEN_LIFETIME' : timedelta(minutes=60),
        'REFRESH_TOKEN_LIFETIME' : timedelta(days=7),

        'ROTATE_REFRESH_TOKENS' : True,
        'BLACKLIST_AFTER_ROTATION' : True, # BLACKLIST OLD TOKEN


        'TOKEN_OBTAIN_SERIALIZER' : 'core.tokens.ExtendedTokenObtainSerializer',
        # which fields are embedded to the token
        'USER_ID_FIELD' : 'id',
        'USER_ID_CLAIM' : 'user_id',
}


LANGUAGE_CODE = 'tr-tr'
TIME_ZONE = 'Europe/Istanbul'

USE_I18N = True # translation active
USE_L10N = True # date, number formats are localized
USER_TZ = True  # all date data are stored as timezone-aware

LOCALE_PATHS = [BASE_DIR / 'locale']




SPECTATULAR_SETTINGS = {
        'TITLE' : 'AutoFlow API',
        'DESCRIPTION' : 'Automation Control panel REST API',
        'VERSION' : '1.0.0',

        # activating auth button on swagger UI
        'SERVE_INCLUDE_SCHEMA' : False,
        'COMPONENT_SPLIT_REQUEST': True,
} 

TEMPLATES = [
        {
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'DIRS': [],
                'APP_DIRS': True,
                'OPTIONS': {
                'context_processors': [
                        'django.template.context_processors.debug',
                        'django.template.context_processors.request',
                        'django.contrib.auth.context_processors.auth',
                        'django.contrib.messages.context_processors.messages',
                ],
                },
        },
]


REDIS_URL = env('REDIS_URL',default='redis://localhost:6379/0')


# redis broker - task manager
CELERY_BORKER_URL = env('REDIS_URL',default='redis://localhost:6379/0')


CELERY_RESULT_BACKEND = 'django_db'

CELERY_TIMEZONE = 'Europe/Istanbul'


# PREVENT MASS PAYLOADS 
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'