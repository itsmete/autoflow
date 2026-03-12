# from apps.tenants.models import User core shouldnt import anyting from outside

ROLE_WEIGHTS = {
	'super_admin': 4,
	'owner': 3,
	'branch_manager': 2,
	'staff': 1,
}

def get_weight(role: str) -> int:
	return ROLE_WEIGHTS.get(role, default = 0)

def can_manage(actor_role: str, target_role: str) -> bool:
	return get_weight(actor_role) > get_weight(target_role)

def get_visible_roles(role :str) -> list : 
	weight = get_weight(role)
	return [r for r,w in ROLE_WEIGHTS.items() if w < weight]


