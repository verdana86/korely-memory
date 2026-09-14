import type {
	IAuthenticateGeneric,
	ICredentialTestRequest,
	ICredentialType,
	INodeProperties,
} from 'n8n-workflow';

export class KorelyApi implements ICredentialType {
	name = 'korelyApi';

	displayName = 'Korely API';

	documentationUrl = 'https://korely.ai/agents/docs/api-reference';

	properties: INodeProperties[] = [
		{
			displayName: 'API Key',
			name: 'apiKey',
			type: 'string',
			typeOptions: { password: true },
			default: '',
			required: true,
			description:
				'A key from the hosted service begins with kor_live_. A key minted by an install you run yourself begins with kor_self_, and needs the base URL below set to that install.',
		},
		{
			// This field is the reason the node exists rather than an HTTP Request
			// node with a header. The same product is a hosted service and a thing
			// you install, and a client with no address picks the hosted one: a
			// self-hosted key then travels to somebody else's server before being
			// refused. Asking for the address up front, next to the key, makes the
			// pair visible at the moment somebody sets it.
			displayName: 'Base URL',
			name: 'baseUrl',
			type: 'string',
			default: 'https://api.korely.ai',
			required: true,
			description:
				'The hosted service, or your own install. If your key begins with kor_self_, this must be your own server.',
		},
	];

	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: {
				Authorization: '=Bearer {{$credentials.apiKey}}',
			},
		},
	};

	// Pressing "Test" asks the server the one question that matters: is this key
	// for this address. A mismatched pair is refused with a message that names
	// the prefix and says what to change, so the test fails with an explanation
	// rather than a blank 401.
	test: ICredentialTestRequest = {
		request: {
			baseURL: '={{$credentials.baseUrl}}',
			url: '/v1/ping',
			method: 'GET',
		},
	};
}
