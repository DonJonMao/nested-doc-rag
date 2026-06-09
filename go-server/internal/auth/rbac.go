package auth

func HasAnyRole(userRoles []string, allowedRoles ...string) bool {
	if len(allowedRoles) == 0 {
		return true
	}
	allowed := make(map[string]struct{}, len(allowedRoles))
	for _, role := range allowedRoles {
		allowed[role] = struct{}{}
	}
	for _, role := range userRoles {
		if _, ok := allowed[role]; ok {
			return true
		}
	}
	return false
}

func ValidGlobalRole(role string) bool {
	switch role {
	case RoleAdmin, RoleOperator, RoleReviewer, RoleViewer:
		return true
	default:
		return false
	}
}

func ValidWorkspaceRole(role string) bool {
	switch role {
	case "owner", "operator", "reviewer", "viewer":
		return true
	default:
		return false
	}
}
