# Channel plans

*20.03.2026 , 20.43*

## Config Schema
### Fields

- **type** (String)
  - ```int``` 
  - ```string``` 
  - ```dict``` 
  - ```boolean``` 
  - ```list``` 

- **required** (Boolean)

- **default** (depends on field)

- **description** (String)

- **choices** (List)

- **fields** (Dict)


### Sample 

```json
SCHEMA = {
       "key1" : {
                "type" : "string",
                "required" : True,
        },
        "key2" : {
                "type" : "string",
                "required" : True,
                "default" : "adc",
                "choices" : ["abc","adc","cba"],
        },
        "key3" : {
                "type" : "dict",
                "required" : True,
                "description": "lorem ipsum sit dolor amet",
                "fields" : {
                        "inner_key1" : {
                                "type" : "string",
                                "default": "sdad",
                        },
                        "inner_key2" : {
                                "type" : "integer",
                                "required" : True
                        }
                }
        }, 
}
```

### Notes / Goals

* Serializers and other methods should be travel the nested objects easily.

* ```automations/``` app will be refactored to be compatible with SCHEMA  logic . All serializers, and other business logic will run on schemas.

### Algorithm

* **For Nested dicts in schema**, ```LEAF KEYS``` might be correct choice. Type is the primitive key for defining a field,    
 


## SMS Provider

### Fields 

* **api_url**
  - type : str
  - required 
* **auth_type**
  - type : string 
  - required True
  - default basic
  - choices [basic,bearer,api_key]
* **auth_credentials**
  - type: dict
  - required : true
  - description : str
* **from_number**
  - type : str
  - required True
* **body_template**
  - dict
  - required_template
  - fields :
    - 

**```body_template```** field for giving the correct arguments for varius API services. Meanwhile , different services may ask the same data but with different names, for which template is 

**Structure of body template :**

```json
template = {
        "To" : "{to}",
        "From" : "{number}" ,
        "Body" : "{message}"
}

/* key : field in remote server,
   value : field in our schema */
```

