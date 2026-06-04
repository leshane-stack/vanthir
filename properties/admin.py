from django.contrib import admin
from properties import models as m

for _model in [
    m.Parcel, m.Building, m.Unit, m.OwnerEntity, m.ManagementCompany, m.HOA,
    m.Violation, m.Permit, m.CourtFiling, m.Deed,
    m.OwnershipSnapshot, m.AssessmentSnapshot, m.FloodZoneSnapshot,
    m.HOAFinancialSnapshot, m.InsuranceRiskSnapshot,
    m.ResidentVerification, m.MaintenanceReport, m.DepositReport,
    m.RentReport, m.IncidentReport,
    m.HealthScore, m.ScoreFactor, m.RawSnapshot,
]:
    admin.site.register(_model)
