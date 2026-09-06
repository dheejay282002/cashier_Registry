from django.db import models


class SystemSetting(models.Model):
	system_name = models.CharField(max_length=120, default="registry")
	entity_name = models.CharField(max_length=200, blank=True, default='')
	address = models.CharField(max_length=300, blank=True, default='')
	system_logo = models.ImageField(upload_to="system_assets/", blank=True, null=True)
	system_logo_hover = models.ImageField(upload_to="system_assets/", blank=True, null=True, help_text="Image shown when hovering over the login logo to prevent downloading")
	last_cheque_number = models.CharField(max_length=6, blank=True, default='')
	last_report_number = models.CharField(max_length=20, blank=True, default='')
	last_dv_payroll_no = models.CharField(max_length=20, blank=True, default='', help_text="Last DV/Payroll number sequence (YYYY-MM-NNNN)")
	last_ors_burs_no = models.CharField(max_length=30, blank=True, default='', help_text="Last ORS/BURS number sequence")
	lockscreen_wallpaper = models.ImageField(upload_to="system_assets/", blank=True, null=True)
	login_background = models.ImageField(upload_to="system_assets/", blank=True, null=True, help_text="Background image shown on the login page")
	auto_lock_seconds = models.IntegerField(default=30, help_text="Idle seconds before auto-lock warning (min 10, max 3600)")
	auto_logout_seconds = models.IntegerField(default=3600, help_text="Idle seconds before automatic logout (min 60, max 86400)")

	# Backup Retention settings
	retention_enabled = models.BooleanField(default=False, help_text="Enable automatic backup retention policy")
	retention_max_age_days = models.IntegerField(default=90, help_text="Delete backups older than this many days")
	retention_max_count = models.IntegerField(default=10, help_text="Maximum number of backups to keep (0 = unlimited)")
	retention_auto_delete = models.BooleanField(default=False, help_text="Automatically delete old backups based on policy")

	# Email / SMTP settings
	email_backend = models.CharField(max_length=200, blank=True, default='django.core.mail.backends.smtp.EmailBackend', help_text="Django email backend class")
	email_host = models.CharField(max_length=200, blank=True, default='smtp.gmail.com', help_text="SMTP server host")
	email_port = models.IntegerField(default=587, help_text="SMTP server port")
	email_use_tls = models.BooleanField(default=True, help_text="Use TLS encryption")
	email_host_user = models.CharField(max_length=200, blank=True, default='', help_text="SMTP username / email address")
	email_host_password = models.CharField(max_length=200, blank=True, default='', help_text="SMTP password or app password")
	default_from_email = models.CharField(max_length=200, blank=True, default='', help_text="Default sender email address")

	def __str__(self):
		return self.system_name

	@classmethod
	def get_settings(cls):
		settings_obj, _ = cls.objects.get_or_create(pk=1, defaults={
			"system_name": "registry",
			"entity_name": "",
			"address": "",
			"auto_lock_seconds": 30,
			"auto_logout_seconds": 3600,
			"retention_enabled": False,
			"retention_max_age_days": 90,
			"retention_max_count": 10,
			"retention_auto_delete": False,
		})
		return settings_obj

	@classmethod
	def next_report_number(cls, prefix=None):
		from django.utils import timezone

		settings_obj = cls.get_settings()
		if not prefix:
			prefix = timezone.localdate().strftime("%Y-%m")
		current = (settings_obj.last_report_number or '').strip()
		sequence = 0
		if current.startswith(prefix) and '-' in current:
			try:
				sequence = int(current.rsplit('-', 1)[-1])
			except Exception:
				sequence = 0
		return f"{prefix}-{sequence + 1:04d}"

	@classmethod
	def record_report_number(cls, report_number):
		settings_obj = cls.get_settings()
		settings_obj.last_report_number = str(report_number or '').strip()
		settings_obj.save(update_fields=['last_report_number'])
		return settings_obj.last_report_number

	@classmethod
	def next_ors_burs_no(cls):
		"""Generate next ORS/BURS No in format: 02-206441-YYYY-MM-NNNNN"""
		from django.utils import timezone
		import re

		settings_obj = cls.get_settings()
		now = timezone.localdate()
		prefix = f"02-206441-{now.year}-{now.month:02d}"

		# Find highest existing ORS/BURS No for current month
		from cashier.models import Cheque
		highest_seq = 0
		pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")

		# Check cheques table
		for val in Cheque.objects.exclude(ors_burs_no='').values_list('ors_burs_no', flat=True):
			m = pattern.match(val or '')
			if m:
				try:
					n = int(m.group(1))
					if n > highest_seq:
						highest_seq = n
				except ValueError:
					pass

		stored = settings_obj.last_ors_burs_no or ''
		stored_match = pattern.match(stored)
		if stored_match:
			try:
				stored_seq = int(stored_match.group(1))
				if stored_seq > highest_seq:
					highest_seq = stored_seq
			except ValueError:
				pass

		next_seq = highest_seq + 1
		result = f"{prefix}-{next_seq:05d}"
		settings_obj.last_ors_burs_no = result
		settings_obj.save(update_fields=['last_ors_burs_no'])
		return result

	@classmethod
	def next_dv_payroll_no(cls, fund_cluster_code='12'):
		"""Generate next DV/Payroll No in format: YYYY-MM-NNNN"""
		from django.utils import timezone
		import re

		settings_obj = cls.get_settings()
		now = timezone.localdate()
		prefix = f"{now.year}-{now.month:02d}"

		# Find highest existing DV/Payroll No for current month
		from cashier.models import Cheque
		highest_seq = 0
		pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")

		# Check cheques table
		for val in Cheque.objects.exclude(dv_payroll_no='').values_list('dv_payroll_no', flat=True):
			m = pattern.match(val or '')
			if m:
				try:
					n = int(m.group(1))
					if n > highest_seq:
						highest_seq = n
				except ValueError:
					pass

		stored = settings_obj.last_dv_payroll_no or ''
		stored_match = pattern.match(stored)
		if stored_match:
			try:
				stored_seq = int(stored_match.group(1))
				if stored_seq > highest_seq:
					highest_seq = stored_seq
			except ValueError:
				pass

		next_seq = highest_seq + 1
		result = f"{prefix}-{next_seq:04d}"
		settings_obj.last_dv_payroll_no = result
		settings_obj.save(update_fields=['last_dv_payroll_no'])
		return result

	@classmethod
	def next_supplier_dv_payroll_no(cls):
		"""Generate next DV/Payroll No for suppliers (same sequence as cheques)"""
		return cls.next_dv_payroll_no()


class AuditLog(models.Model):
    admin = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=200)
    details = models.JSONField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.timestamp.isoformat()} - {self.action}"



class AccountTitleGroup(models.Model):
    name       = models.CharField(max_length=255, unique=True)
    uacs       = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ManagementOption(models.Model):
	CATEGORY_ACCOUNT_TITLE = 'account_title'
	CATEGORY_REMARK = 'remark'
	CATEGORY_TAX = 'tax'
	CATEGORY_CHOICES = (
		(CATEGORY_ACCOUNT_TITLE, 'Account Title'),
		(CATEGORY_REMARK, 'Remark'),
		(CATEGORY_TAX, 'Tax'),
	)

	category   = models.CharField(max_length=40, choices=CATEGORY_CHOICES, db_index=True)
	value      = models.CharField(max_length=255)
	uacs       = models.CharField(max_length=100, blank=True, default='')
	group      = models.ForeignKey(
		AccountTitleGroup,
		on_delete=models.SET_NULL,
		null=True, blank=True,
		related_name='options',
	)
	is_active  = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['category', 'value']
		unique_together = ('category', 'value')

	def __str__(self):
		return f"{self.get_category_display()}: {self.value}"


class ChatbotConfig(models.Model):
	PROVIDER_CHOICES = (
		('openai', 'OpenAI'),
		('anthropic', 'Anthropic (Claude)'),
		('gemini', 'Google Gemini'),
		('groq', 'Groq'),
		('deepseek', 'DeepSeek'),
		('mistral', 'Mistral'),
		('xai', 'xAI (Grok)'),
		('openrouter', 'OpenRouter'),
		('together', 'Together AI'),
		('cohere', 'Cohere'),
		('huggingface', 'Hugging Face'),
		('ollama', 'Ollama (Local)'),
		('custom', 'Custom (OpenAI-Compatible)'),
	)

	name = models.CharField(max_length=100, default="AI Assistant", help_text="Name of the chatbot assistant")
	provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default='openai')
	api_key = models.CharField(max_length=500, blank=True, default='', help_text="API key for the AI provider")
	base_url = models.CharField(max_length=500, blank=True, default='', help_text="Custom base URL (for Ollama, Custom, or self-hosted endpoints)")
	model_name = models.CharField(max_length=100, blank=True, default='gpt-4o-mini', help_text="Model name (e.g. gpt-4o-mini, claude-3-haiku, gemini-2.0-flash)")
	system_prompt = models.TextField(blank=True, default='', help_text="System prompt that defines the assistant's behavior")
	welcome_message = models.CharField(max_length=500, blank=True, default='Hi! I\'m your AI assistant. How can I help you today?', help_text="First message shown to users")
	is_active = models.BooleanField(default=False, help_text="Enable the chatbot for users")
	max_history = models.IntegerField(default=20, help_text="Maximum number of messages to keep in context")

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Chatbot Configuration'
		verbose_name_plural = 'Chatbot Configurations'

	def __str__(self):
		return f"{self.name} ({self.get_provider_display()})"

	@classmethod
	def get_config(cls):
		config, _ = cls.objects.get_or_create(pk=1, defaults={
			"name": "AI Assistant",
			"provider": "openai",
			"is_active": False,
		})
		return config


class ChatMessage(models.Model):
	ROLE_CHOICES = (
		('user', 'User'),
		('assistant', 'Assistant'),
	)

	user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='chat_messages')
	role = models.CharField(max_length=10, choices=ROLE_CHOICES)
	content = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['created_at']

	def __str__(self):
		return f"{self.user.username} - {self.role}: {self.content[:50]}"


class UserMessage(models.Model):
	sender = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='sent_messages')
	recipient = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='received_messages')
	content = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)
	read = models.BooleanField(default=False)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"{self.sender.username} -> {self.recipient.username}: {self.content[:50]}"
