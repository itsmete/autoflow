from rest_framework_simplejwt.tokens import AccessToken,RefreshToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

"""
Interior of access tokens 

Token (Base)
|
-----> Access Token


Base Token class do those stuffs : Signing , Time management, and most importantly : using payload as dict

token = AccessToken()
token['email'] = 'asdsada@adsdsa.com' like  dict

Tokens is a object but its inside is a dict , which is payload.


token = ExtendedAccesToken.for_user(user)


basically : 

@classmethod
def for_user(cls, user):
    token = cls()                          # creates token object
    token[api_settings.USER_ID_CLAIM] = \ 
        getattr(user, api_settings.USER_ID_FIELD)  # put only user_id
    return token

    
'USER_ID_CLAIM' is defined as 'user_id' in settings (config/settings/base.py)
e.g Payload is like ;

 {
        "user_id" : "uuid",
        "exp": 234567,
        "iat" :234567,
        "jti" : "unique-token-id"
 
 }


"""


class ExtendedAccessToken(AccessToken):
        @classmethod
        def for_user(cls,user):

                token = super().for_user(user)

                token['role'] = user.role
                token['email'] = user.email
                token['tenant_id'] = str(user.tenant.id) if user.tenant else None
                token['branch_id'] = str(user.branch.id) if user.branch else None

                return token
        

class ExtendedTokenObtainSerializer(TokenObtainPairSerializer):
        token_class = ExtendedAccessToken # the new cls.token_class , it was default AccessToken

        @classmethod
        def get_token(cls, user):
                token = super().get_token(user)
                # above function has inside cls.token_class.for_user(user), so it will take our extended token class
                return token
        
class ExtendedRefreshToken(RefreshToken):
        access_token_class = ExtendedAccessToken