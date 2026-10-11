/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.nageoffer.ai.ragent.rag.config;

import co.elastic.clients.elasticsearch.ElasticsearchClient;
import co.elastic.clients.json.jackson.JacksonJsonpMapper;
import co.elastic.clients.transport.instrumentation.Instrumentation;
import co.elastic.clients.transport.instrumentation.NoopInstrumentation;
import co.elastic.clients.transport.instrumentation.OpenTelemetryForElasticsearch;
import co.elastic.clients.transport.rest_client.RestClientTransport;
import io.opentelemetry.api.OpenTelemetry;
import org.apache.http.HttpHost;
import org.apache.http.auth.AuthScope;
import org.apache.http.auth.UsernamePasswordCredentials;
import org.apache.http.impl.client.BasicCredentialsProvider;
import org.elasticsearch.client.RestClient;
import org.elasticsearch.client.RestClientBuilder;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;

import java.util.Arrays;

/**
 * Elasticsearch 客户端配置
 */
@Slf4j
@Configuration
@ConditionalOnClass(ElasticsearchClient.class)
@ConditionalOnProperty(name = "rag.keyword.type", havingValue = "es")
public class EsClientConfig {

    @Bean(destroyMethod = "close")
    public RestClient keywordRestClient(KeywordProperties keywordProperties) {
        String uris = keywordProperties.getEs().getUris();
        HttpHost[] hosts = Arrays.stream(StringUtils.commaDelimitedListToStringArray(uris))
                .map(String::trim)
                .filter(StringUtils::hasText)
                .map(HttpHost::create)
                .toArray(HttpHost[]::new);

        String username = keywordProperties.getEs().getUsername();
        String password = keywordProperties.getEs().getPassword();
        if (StringUtils.hasText(username) != StringUtils.hasText(password)) {
            throw new IllegalArgumentException("ES username 和 password 需要同时配置");
        }
        RestClientBuilder builder = RestClient.builder(hosts);
        if (StringUtils.hasText(username)) {
            BasicCredentialsProvider credentials = new BasicCredentialsProvider();
            credentials.setCredentials(AuthScope.ANY, new UsernamePasswordCredentials(username, password));
            builder.setHttpClientConfigCallback(client -> client.setDefaultCredentialsProvider(credentials));
        }
        log.info("Elasticsearch 关键词检索客户端已初始化, uris={}", uris);
        return builder.build();
    }

    @Bean
    public ElasticsearchClient elasticsearchClient(RestClient keywordRestClient,
                                                  ObjectProvider<OpenTelemetry> openTelemetryProvider) {
        // 先解析项目 SDK，避免 ES 默认 instrumentation 提前读取 GlobalOpenTelemetry，
        // 将全局实例锁定为 no-op，阻止随后注册 Langfuse SDK。关闭追踪时也不读取全局实例。
        OpenTelemetry openTelemetry = openTelemetryProvider.getIfAvailable();
        Instrumentation instrumentation = openTelemetry == null
                ? NoopInstrumentation.INSTANCE
                : new OpenTelemetryForElasticsearch(openTelemetry, false);
        RestClientTransport transport = new RestClientTransport(keywordRestClient, new JacksonJsonpMapper(),
                null, instrumentation);
        return new ElasticsearchClient(transport);
    }
}
