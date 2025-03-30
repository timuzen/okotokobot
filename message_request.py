import requests
url = "http://api.forismatic.com/api/1.0/"
params = {
    "method": "getQuote", "format": "json", "lang": "ru",
}

response = requests.post(url, data=params)


if response.status_code == 200:
    data = response.json()
    print(data['quoteText'])
else:
    print("Сегодня нет мыслей")




