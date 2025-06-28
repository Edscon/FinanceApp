from django.db import models

class Company(models.Model):
    ticker = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=128, blank=True, null=True)
    currency = models.CharField(max_length=8, default='USD')
    exchange = models.CharField(max_length=32, blank=True, null=True)

    def __str__(self):
        return self.ticker

class FundamentalsQuarter(models.Model):
    """
    Un registre per trimestre i companyia amb totes les xifres de l'income‑statement.
    """
    company  = models.ForeignKey('screener.Company', on_delete=models.CASCADE, related_name='quarters')

    # Metadades del període
    period_end  = models.DateField(db_index=True, null=True)          # 'date' (fi de trimestre)
    fiscal_year = models.PositiveIntegerField()
    period      = models.CharField(max_length=4)           # Q1 / Q2 / Q3 / Q4
    reported_currency = models.CharField(max_length=8)
    cik         = models.CharField(max_length=16, null=True)
    filing_date = models.DateField(null=True)
    accepted_date = models.DateTimeField(null=True)

    # Income‑statement
    revenue                               = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    cost_of_revenue                       = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    gross_profit                          = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    research_and_development_expenses     = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    general_and_administrative_expenses   = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    selling_and_marketing_expenses        = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    selling_general_and_administrative_expenses = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    other_expenses                        = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    operating_expenses                    = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    cost_and_expenses                     = models.DecimalField(max_digits=20, decimal_places=0, null=True)

    # Finançament / interessos
    net_interest_income                   = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    interest_income                       = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    interest_expense                      = models.DecimalField(max_digits=20, decimal_places=0, null=True)

    # Resultats operatius
    depreciation_and_amortization         = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    ebitda                                = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    ebit                                  = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    non_operating_income_excl_interest    = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    operating_income                      = models.DecimalField(max_digits=20, decimal_places=0, null=True)

    # Altres ingressos / despeses
    total_other_income_expenses_net       = models.DecimalField(max_digits=20, decimal_places=0, null=True)

    # Resultat abans d'impostos i final
    income_before_tax                     = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    income_tax_expense                    = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    net_income_from_continuing_ops        = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    net_income_from_discontinued_ops      = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    other_adjustments_to_net_income       = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    net_income                            = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    net_income_deductions                 = models.DecimalField(max_digits=20, decimal_places=0, null=True)
    bottom_line_net_income                = models.DecimalField(max_digits=20, decimal_places=0, null=True)

    # EPS i accions
    eps                                   = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    eps_diluted                           = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    weighted_average_shs_out              = models.BigIntegerField(null=True)
    weighted_average_shs_out_dil          = models.BigIntegerField(null=True)

    # Dividends
    dividend = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    adj_dividend = models.DecimalField(max_digits=10, decimal_places=4, null=True)

    class Meta:
        unique_together = ['company', 'period_end']
        indexes = [models.Index(fields=['company', 'period_end'])]

    def __str__(self):
        return f"{self.company.ticker} – {self.period} {self.fiscal_year}"