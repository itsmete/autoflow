

"""
BaseActionSerilizer,
        Obligotary
                Config > each action must have a specific config
                action_type
                order
                on_failure
        
        
EmailActionSerializer
        Obgl
                config:
                        to - recevier
                        subject - 
                        body

WebhookActionSerializer 
        obgl
                config
                        url
                        method
                        headers
                        payload

        
                


"""


from rest_framework import serializers
from abc import abstractmethod



class BaseActionSerilaizer(serializers.Serializer):
        
        #That abstract method means that 
        # Whoever inhertitances this class have to write down a validate_config() method
        #If not provided, class is not going to be initalized,



        @abstractmethod
        def validate_config(self,value):
                pass

     