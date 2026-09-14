import type {
	IExecuteFunctions,
	IDataObject,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	IHttpRequestMethods,
	IHttpRequestOptions,
	JsonObject,
} from 'n8n-workflow';
import { NodeApiError, NodeOperationError } from 'n8n-workflow';

/**
 * Korely for n8n.
 *
 * The page that used to describe this integration told people to use the HTTP
 * Request node with a bearer header, which works and hides the one thing that
 * goes wrong: the same product is a hosted service and a thing you install, and
 * a client with no address picks the hosted one. A self-hosted key then travels
 * to somebody else's server before being refused. Here the address sits next to
 * the key in the credential, and pressing Test asks the server whether the pair
 * makes sense.
 *
 * No runtime dependencies, on purpose: n8n does not allow them in a verified
 * community node, and this calls the REST API through n8n's own request helper.
 */
export class Korely implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Korely',
		name: 'korely',
		icon: 'file:korely.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"] + ": " + $parameter["resource"]}}',
		description: 'Bi-temporal memory: store what happened, ask what was true on a date',
		defaults: { name: 'Korely' },
		inputs: ['main'] as never,
		outputs: ['main'] as never,
		credentials: [{ name: 'korelyApi', required: true }],
		requestDefaults: { baseURL: '={{$credentials.baseUrl}}' },
		properties: [
			{
				displayName: 'Resource',
				name: 'resource',
				type: 'options',
				noDataExpression: true,
				options: [
					{ name: 'Memory', value: 'memory' },
					{ name: 'Fact', value: 'fact' },
					{ name: 'Context', value: 'context' },
				],
				default: 'memory',
			},

			// ── memory ────────────────────────────────────────────────────────
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				displayOptions: { show: { resource: ['memory'] } },
				options: [
					{ name: 'Add', value: 'add', action: 'Store a memory', description: 'Store text and mine typed facts from it' },
					{ name: 'Search', value: 'search', action: 'Search memories', description: 'Find memories by meaning' },
				],
				default: 'add',
			},
			{
				displayName: 'Content',
				name: 'content',
				type: 'string',
				typeOptions: { rows: 3 },
				default: '',
				required: true,
				displayOptions: { show: { resource: ['memory'], operation: ['add'] } },
				description: 'The text to remember',
			},
			{
				displayName: 'Query',
				name: 'query',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { resource: ['memory'], operation: ['search'] } },
			},

			// ── fact ──────────────────────────────────────────────────────────
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				displayOptions: { show: { resource: ['fact'] } },
				options: [
					{ name: 'List', value: 'list', action: 'List facts', description: 'What is true now, or what was true on a date' },
					{ name: 'Write', value: 'write', action: 'Write a fact', description: 'Assert a typed triple directly, with no model involved' },
					{ name: 'Close', value: 'forget', action: 'Close a fact', description: 'It stops being current and stays in history' },
					{ name: 'Correct', value: 'correct', action: 'Correct a fact', description: 'Supersede it; both stay readable' },
				],
				default: 'list',
			},
			{
				displayName: 'Fact ID',
				name: 'factId',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { resource: ['fact'], operation: ['forget', 'correct'] } },
			},
			{
				displayName: 'Stopped Being True On',
				name: 'at',
				type: 'string',
				default: '',
				placeholder: '2026-06-20',
				displayOptions: { show: { resource: ['fact'], operation: ['forget'] } },
				description:
					'The date it stopped being true, not the date you noticed. Leave empty for now. Reading as_of before this date still returns the fact.',
			},
			{
				displayName: 'Subject',
				name: 'subject',
				type: 'string',
				default: '',
				displayOptions: { show: { resource: ['fact'], operation: ['write', 'correct'] } },
			},
			{
				displayName: 'Predicate',
				name: 'predicate',
				type: 'string',
				default: '',
				displayOptions: { show: { resource: ['fact'], operation: ['write', 'correct'] } },
			},
			{
				displayName: 'Object',
				name: 'object',
				type: 'string',
				default: '',
				displayOptions: { show: { resource: ['fact'], operation: ['write', 'correct'] } },
			},
			{
				displayName: 'True From',
				name: 'validFrom',
				type: 'string',
				default: '',
				placeholder: '2026-03-15',
				displayOptions: { show: { resource: ['fact'], operation: ['write'] } },
				description:
					'When this became true. Empty means now. Set it when you are backfilling, or the fact lands on today and a later close can end up before its own start.',
			},
			{
				displayName: 'As Of',
				name: 'asOf',
				type: 'string',
				default: '',
				placeholder: '2026-04-01',
				displayOptions: { show: { resource: ['fact'], operation: ['list'] } },
				description: 'Read the state as it was on this date. Empty means now.',
			},

			// ── context ───────────────────────────────────────────────────────
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				displayOptions: { show: { resource: ['context'] } },
				options: [
					{ name: 'Get', value: 'get', action: 'Get prompt ready context', description: 'Facts and memories assembled into a block for a prompt' },
				],
				default: 'get',
			},
			{
				displayName: 'Query',
				name: 'query',
				type: 'string',
				default: '',
				required: true,
				displayOptions: { show: { resource: ['context'] } },
			},

			// ── shared ────────────────────────────────────────────────────────
			{
				displayName: 'End User ID',
				name: 'userId',
				type: 'string',
				default: '',
				description:
					'Who this is about. Everything is scoped to it, so leaving it empty files the row under nobody and reads scoped to a user will not find it.',
			},
			{
				displayName: 'Additional Fields',
				name: 'extra',
				type: 'collection',
				placeholder: 'Add field',
				default: {},
				options: [
					{ displayName: 'Agent ID', name: 'agent_id', type: 'string', default: '' },
					{ displayName: 'Run ID', name: 'run_id', type: 'string', default: '' },
					{ displayName: 'Limit', name: 'limit', type: 'number', default: 15 },
					{
						displayName: 'Happened On',
						name: 'timestamp',
						type: 'string',
						default: '',
						placeholder: '2026-03-15',
						description:
							'When the events in this memory happened. Facts mined from it inherit the date, so a backfilled memory lands on the timeline where it belongs instead of today.',
					},
				],
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const out: INodeExecutionData[] = [];

		for (let i = 0; i < items.length; i++) {
			try {
				const resource = this.getNodeParameter('resource', i) as string;
				const operation = this.getNodeParameter('operation', i) as string;
				const userId = (this.getNodeParameter('userId', i, '') as string).trim();
				const extra = this.getNodeParameter('extra', i, {}) as IDataObject;

				let method: IHttpRequestMethods = 'GET';
				let url = '';
				let body: IDataObject | undefined;
				let qs: IDataObject | undefined;

				const scope: IDataObject = {};
				if (userId) scope.user_id = userId;
				if (extra.agent_id) scope.agent_id = extra.agent_id;
				if (extra.run_id) scope.run_id = extra.run_id;

				if (resource === 'memory' && operation === 'add') {
					method = 'POST';
					url = '/v1/memories';
					body = { content: this.getNodeParameter('content', i) as string, ...scope };
					if (extra.timestamp) body.timestamp = extra.timestamp;
				} else if (resource === 'memory' && operation === 'search') {
					method = 'POST';
					url = '/v1/memories/search';
					body = { query: this.getNodeParameter('query', i) as string, ...scope };
					if (extra.limit) body.limit = extra.limit;
				} else if (resource === 'context') {
					url = '/v1/context';
					qs = { query: this.getNodeParameter('query', i) as string, ...scope };
				} else if (resource === 'fact' && operation === 'list') {
					url = '/v1/facts';
					qs = { ...scope };
					const asOf = (this.getNodeParameter('asOf', i, '') as string).trim();
					if (asOf) qs.as_of = asOf;
					if (extra.limit) qs.limit = extra.limit;
				} else if (resource === 'fact' && operation === 'write') {
					method = 'POST';
					url = '/v1/facts';
					body = {
						subject: this.getNodeParameter('subject', i) as string,
						predicate: this.getNodeParameter('predicate', i) as string,
						object: this.getNodeParameter('object', i) as string,
						...scope,
					};
					const from = (this.getNodeParameter('validFrom', i, '') as string).trim();
					if (from) body.valid_from = from;
				} else if (resource === 'fact' && operation === 'forget') {
					method = 'POST';
					url = `/v1/facts/${encodeURIComponent(this.getNodeParameter('factId', i) as string)}/forget`;
					const at = (this.getNodeParameter('at', i, '') as string).trim();
					body = at ? { at } : {};
				} else if (resource === 'fact' && operation === 'correct') {
					method = 'PATCH';
					url = `/v1/facts/${encodeURIComponent(this.getNodeParameter('factId', i) as string)}`;
					body = {};
					for (const f of ['subject', 'predicate', 'object'] as const) {
						const v = (this.getNodeParameter(f, i, '') as string).trim();
						if (v) body[f] = v;
					}
					if (Object.keys(body).length === 0) {
						throw new NodeOperationError(
							this.getNode(),
							'A correction needs at least one of subject, predicate or object. A correction that changes nothing is a mistake, not a no-op.',
							{ itemIndex: i },
						);
					}
				} else {
					throw new NodeOperationError(
						this.getNode(),
						`Unknown operation ${resource}:${operation}`,
						{ itemIndex: i },
					);
				}

				const options: IHttpRequestOptions = { method, url, json: true };
				if (body !== undefined) options.body = body;
				if (qs !== undefined) options.qs = qs;

				const response = await this.helpers.httpRequestWithAuthentication.call(
					this,
					'korelyApi',
					options,
				);
				out.push({ json: response as IDataObject, pairedItem: { item: i } });
			} catch (error) {
				if (this.continueOnFail()) {
					out.push({ json: { error: (error as Error).message }, pairedItem: { item: i } });
					continue;
				}
				if (error instanceof NodeOperationError) throw error;
				throw new NodeApiError(this.getNode(), error as JsonObject, { itemIndex: i });
			}
		}

		return [out];
	}
}
