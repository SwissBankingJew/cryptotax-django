from django.contrib import admin
from .models import WalletAnalysisOrder, SolanaPayment, DuneQueryJob, ReportFile
# Register your models here.


admin.site.register(WalletAnalysisOrder)
admin.site.register(SolanaPayment)
admin.site.register(DuneQueryJob)
admin.site.register(ReportFile)


