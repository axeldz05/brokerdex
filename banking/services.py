class ExternalBankService:
    def send_money(self, source_cvu, target_cvu, amount):
        # Here should be the api for the bank, like: request.post('https://api.bancocentral...', ...)
        return True
