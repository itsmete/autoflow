FIELDS = ['type','description','required','default','choices','fields','validator','error_message',"required_at_runtime","source"]


TYPE_MAP = {
        'integer' : int,
        'string' : str,
        'boolean' : bool,
        'dict' : dict,
        'list' : list,
}

FIELD_META_SCHEMA = {
        "type": {
                "type": "string",
                "required": True,
                "choices": TYPE_MAP.keys()
        },
        "required": {
                "type": "boolean",
                "required": True,
        },
        "description": {
                "type": "string",
                "required": True,  
        },
        "source": {
                "type": "string",
                "required": True,
                "choices": ["payload", "config", "ai"]
        },
        "default" : {
                "type" : "boolean",
                "required" : False,
        },
        "choices" : {
                "type" : "list",
                "required" : False,
        },

}


def get_field_type(ftype):
        return TYPE_MAP.get(ftype)
