FIELDS = ['type','description','required','default','choices','fields','validator','error_message']


TYPE_MAP = {
        'integer' : int,
        'string' : str,
        'boolean' : bool,
        'dict' : dict,
        'list' : list,       
}


def get_field_type(ftype):
        return TYPE_MAP.get(ftype)
