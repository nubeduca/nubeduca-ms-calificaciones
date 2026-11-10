import os
import json
import boto3

lambda_client = boto3.client('lambda')


def validar_token(event):
    # Modo desarrollo: sin validación, se usan los datos del body
    if os.environ.get('VALIDAR_TOKEN', 'true').lower() != 'true':
        body = event.get('body') or {}
        return {
            'statusCode': 200,
            'tenant_id': body.get('tenant_id', 'UTEC'),
            'email': body.get('email', 'docente.demo@utec.edu.pe'),
            'rol': body.get('rol', 'TEACHER')
        }

    token = (event.get('headers') or {}).get('Authorization')
    if not token:
        return {'statusCode': 403, 'mensaje': 'Falta el token'}

    respuesta = lambda_client.invoke(
        FunctionName=os.environ['VALIDAR_TOKEN_FUNCION'],
        InvocationType='RequestResponse',
        Payload=json.dumps({'token': token})
    )
    return json.loads(respuesta['Payload'].read())